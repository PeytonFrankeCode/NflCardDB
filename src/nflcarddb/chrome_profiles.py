"""Work out which Chrome profile is actually signed in to eBay.

Chrome keeps several profiles side by side under "User Data" -- Default,
Profile 1, Profile 2 -- one per person or work/personal split. Launching the
wrong one opens a browser with no eBay session, which lands on the sign-in page
and looks exactly like being blocked. Guessing "Default" is a coin flip.

Cookie *values* are encrypted and are not read here. Cookie *hostnames* are not:
each profile's Cookies file is an ordinary SQLite database whose host_key column
is plain text. Counting eBay hostnames per profile identifies the signed-in one
without decrypting anything, and without touching a password.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Where a profile keeps its cookie database, newer layout first.
COOKIE_PATHS = ("Network/Cookies", "Cookies")


@dataclass
class ChromeProfile:
    directory: str          # "Default", "Profile 1", ...
    name: str               # display name Chrome shows
    ebay_cookies: int = 0
    signed_in_hint: bool = False

    def describe(self) -> str:
        who = f"{self.directory}" + (f" ({self.name})" if self.name else "")
        if self.ebay_cookies:
            return f"{who} -- {self.ebay_cookies} eBay cookies"
        return f"{who} -- no eBay cookies"


def _display_names(user_data_dir: Path) -> dict:
    """Read profile display names out of Local State (plain JSON)."""
    state = user_data_dir / "Local State"
    if not state.exists():
        return {}
    try:
        data = json.loads(state.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    cache = (data.get("profile") or {}).get("info_cache") or {}
    return {k: (v or {}).get("name", "") for k, v in cache.items()}


def _count_ebay_cookies(profile_dir: Path) -> tuple[int, bool]:
    """Count eBay hostnames in a profile's cookie DB. Values stay encrypted."""
    for rel in COOKIE_PATHS:
        db = profile_dir / rel
        if not db.exists():
            continue
        # Copy first: Chrome may hold a lock, and a diagnostic must never risk
        # writing to the real profile.
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "cookies.sqlite"
            try:
                shutil.copy2(db, copy)
                conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
                rows = conn.execute(
                    "SELECT host_key, name FROM cookies WHERE host_key LIKE '%ebay%'"
                ).fetchall()
                conn.close()
            except (OSError, sqlite3.Error) as exc:
                log.debug("could not read cookies in %s: %s", profile_dir.name, exc)
                continue
        # These are the names eBay uses for a logged-in session.
        session_names = {"ebay", "ns1", "npii", "dp1", "sgnedin"}
        signed = any((n or "").lower() in session_names for _, n in rows)
        return (len(rows), signed)
    return (0, False)


def list_profiles(user_data_dir: str | Path) -> list[ChromeProfile]:
    """Every Chrome profile under a User Data directory, richest in eBay first."""
    root = Path(user_data_dir)
    if not root.exists():
        return []

    names = _display_names(root)
    found: list[ChromeProfile] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name != "Default" and not child.name.startswith("Profile "):
            continue
        count, signed = _count_ebay_cookies(child)
        found.append(ChromeProfile(child.name, names.get(child.name, ""), count, signed))

    found.sort(key=lambda p: (p.signed_in_hint, p.ebay_cookies), reverse=True)
    return found


def pick_ebay_profile(user_data_dir: str | Path) -> Optional[ChromeProfile]:
    """The profile most likely signed in to eBay, or None if none look it."""
    profiles = list_profiles(user_data_dir)
    best = next((p for p in profiles if p.ebay_cookies > 0), None)
    if best:
        log.info("eBay session looks to be in Chrome profile %r (%d cookies)",
                 best.directory, best.ebay_cookies)
    return best
