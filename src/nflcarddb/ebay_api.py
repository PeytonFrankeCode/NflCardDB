"""eBay's Browse API, used as a card catalogue rather than a price source.

**This does not provide sold prices.** Sold data lives behind Marketplace
Insights, which Peyton's application was refused twice, and that is why the
collector scrapes. Nothing here changes that.

What it does provide is the thing three rounds of scraping the search sidebar
could not: eBay's own classification of trading cards, complete and structured.
`fieldgroups=ASPECT_REFINEMENTS` returns every aspect for a query -- Set,
Parallel/Variety, Player/Athlete, Season -- with every value and a match count,
as JSON. The sidebar renders eight of each; the API returns the list.

Active listings carry the same cards as sold ones. The same sets are being sold
right now that were sold last week, so a vocabulary built from active listings
applies directly to sold titles. Prices do not transfer; names do.

The output shape deliberately matches `facets.harvest`, so everything already
built on it -- merging across runs, cleaning, bucketing into vocabularies --
works unchanged with a better source underneath.

Written against eBay's published response format rather than a live call, since
nothing here can reach api.ebay.com. That is a documented contract rather than
scraped markup, so the risk is far lower than guessing at a sidebar -- but the
first real call is still the one that confirms it, and the CLI reports exactly
what came back rather than assuming.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Optional

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

# The only scope needed to read public listing data. Deliberately minimal: this
# reads a catalogue, and a token that can do more than that is a token that can
# do more than that by accident.
SCOPE = "https://api.ebay.com/oauth/api_scope"

MARKETPLACE = "EBAY_US"

# Where the App ID and Cert ID live. Gitignored, same as the Cloudflare token.
CREDENTIALS_FILE = "data/ebay-api.txt"

# Refresh a little early rather than discovering expiry mid-run.
TOKEN_MARGIN_SECONDS = 120


class EbayApiError(RuntimeError):
    """The API refused, with whatever it said about why."""


def load_credentials(path: str = CREDENTIALS_FILE) -> Optional[tuple[str, str]]:
    """Read the App ID and Cert ID, if they have been saved."""
    file = Path(path)
    if not file.exists():
        return None
    values: dict[str, str] = {}
    for line in file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip().lower()] = value.strip()
    app_id, cert_id = values.get("app_id"), values.get("cert_id")
    return (app_id, cert_id) if app_id and cert_id else None


def save_credentials(app_id: str, cert_id: str,
                     path: str = CREDENTIALS_FILE) -> Path:
    """Save them so nothing asks again."""
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        "# eBay developer keys. This file is gitignored -- it is a credential.\n"
        f"app_id={app_id}\ncert_id={cert_id}\n",
        encoding="utf-8",
    )
    return file


class BrowseClient:
    """A thin client over the two calls this project needs."""

    def __init__(self, app_id: str, cert_id: str, timeout: float = 30.0):
        self._app_id = app_id
        self._cert_id = cert_id
        self._timeout = timeout
        self._token: Optional[str] = None
        self._expires_at = 0.0

    def _authorise(self) -> str:
        """Client-credentials grant. Application token, no user involved."""
        if self._token and time.time() < self._expires_at:
            return self._token

        import requests

        basic = base64.b64encode(
            f"{self._app_id}:{self._cert_id}".encode()).decode()
        response = requests.post(
            TOKEN_URL,
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": SCOPE},
            timeout=self._timeout,
        )
        if response.status_code != 200:
            raise EbayApiError(
                f"eBay refused the keys ({response.status_code}). "
                f"{_explain(response)}"
            )
        payload = response.json()
        self._token = payload["access_token"]
        # `expires_in` is seconds; typically two hours.
        self._expires_at = time.time() + payload.get("expires_in", 7200) \
            - TOKEN_MARGIN_SECONDS
        return self._token

    def refinements(self, query: str, category_id: Optional[str] = None,
                    aspect_filter: Optional[str] = None) -> dict:
        """Raw refinement payload for a search: every aspect and every value.

        `limit=1` because the listings are not wanted -- only the aspect
        distributions computed over the whole result set, which eBay returns
        regardless of how many items are asked for.
        """
        import requests

        params = {
            "q": query,
            "limit": "1",
            "fieldgroups": "ASPECT_REFINEMENTS,BUYING_OPTION_REFINEMENTS",
        }
        if category_id:
            params["category_ids"] = category_id
        if aspect_filter:
            params["aspect_filter"] = aspect_filter

        response = requests.get(
            BROWSE_URL,
            headers={"Authorization": f"Bearer {self._authorise()}",
                     "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE},
            params=params,
            timeout=self._timeout,
        )
        if response.status_code != 200:
            raise EbayApiError(
                f"search failed ({response.status_code}). {_explain(response)}")
        return response.json()


def _explain(response) -> str:
    """eBay's own error text, which is usually specific and worth showing."""
    try:
        payload = response.json()
    except ValueError:
        return (response.text or "")[:300]
    errors = payload.get("errors") or []
    if errors:
        first = errors[0]
        return f"{first.get('message', '')} {first.get('longMessage', '')}".strip()
    return json.dumps(payload)[:300]


def aspects_from_payload(payload: dict) -> dict[str, dict[str, Optional[int]]]:
    """Turn a Browse response into the shape `facets` already accumulates.

    Same {aspect: {value: count}} as scraping produces, so merging, cleaning
    and bucketing all work unchanged -- the API is a better source for the
    store, not a second store.
    """
    out: dict[str, dict[str, Optional[int]]] = {}
    refinement = payload.get("refinement") or {}
    for distribution in refinement.get("aspectDistributions") or []:
        name = distribution.get("localizedAspectName")
        if not name:
            continue
        values: dict[str, Optional[int]] = {}
        for entry in distribution.get("aspectValueDistributions") or []:
            value = entry.get("localizedAspectValue")
            if not value:
                continue
            count = entry.get("matchCount")
            values[value] = int(count) if isinstance(count, (int, str)) and \
                str(count).isdigit() else None
        if values:
            out[name] = dict(sorted(values.items(),
                                    key=lambda kv: (-(kv[1] or 0), kv[0])))
    return out


def aspect_filter_for(category_id: Optional[str],
                      pairs: "list[tuple[str, str]]") -> Optional[str]:
    """eBay's aspect_filter syntax, which is not a plain query parameter.

    It reads `categoryId:<id>,Aspect:{Value},Other:{Value}` -- the category is
    required as the first element, braces are required around values, and
    commas separate clauses. A value containing a comma or a brace would break
    that encoding, so such values are dropped rather than sent malformed.
    """
    usable = [(a, v) for a, v in pairs
              if not any(ch in v for ch in ",{}") and not any(ch in a for ch in ",{}")]
    if not usable:
        return None
    clauses = [f"{a}:{{{v}}}" for a, v in usable]
    if category_id:
        clauses.insert(0, f"categoryId:{category_id}")
    return ",".join(clauses)
