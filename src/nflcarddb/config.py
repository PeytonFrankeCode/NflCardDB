"""Configuration loading with sane defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

DEFAULT_CONFIG_PATH = Path("config/queries.yml")

DEFAULT_BANDS: list[list[Optional[float]]] = [
    [None, 10], [10, 25], [25, 50], [50, 100],
    [100, 250], [250, 1000], [1000, None],
]


@dataclass
class QuerySpec:
    id: str
    keywords: str = ""
    category: Optional[str] = None
    extra: dict = field(default_factory=dict)
    bands: Optional[list[list[Optional[float]]]] = None


@dataclass
class FetchConfig:
    delay: float = 2.5
    jitter: float = 1.0
    max_retries: int = 4
    timeout: float = 30.0
    page_budget: Optional[int] = 600
    items_per_page: int = 240
    max_pages_per_segment: int = 42
    max_subdivide_depth: int = 3
    # For a day in the older half of eBay's window, try reaching it by sorting
    # oldest-ended first, which is far fewer pages than paging back from today.
    # Verified with one request per run before it is relied on; set false to
    # force the old behaviour.
    try_oldest_first: bool = True
    # Skip downloading images, fonts and video. The parser reads markup, and
    # photo URLs live in the `src` attribute whether or not the bytes arrive --
    # so this is most of the page load for nothing lost.
    block_media: bool = True
    # Bot checks in a row on one request before it counts as a real block.
    challenge_retries: int = 4
    user_agent: Optional[str] = None
    # auto | requests | browser. "auto" tries the light HTTP client and switches
    # to a real browser if eBay refuses it.
    engine: str = "auto"
    # Use the everyday Chrome profile -- the one already signed in to eBay --
    # instead of a profile belonging to this project. Chrome must be closed
    # while a run is in progress, because it locks its own profile.
    chrome_profile: bool = False


@dataclass
class Config:
    database: str = "data/nflcarddb.sqlite"
    roster: Optional[str] = None
    inserts: Optional[str] = None
    fetch: FetchConfig = field(default_factory=FetchConfig)
    price_bands: list[list[Optional[float]]] = field(default_factory=lambda: list(DEFAULT_BANDS))
    queries: list[QuerySpec] = field(default_factory=list)

    def bands_for(self, query: QuerySpec) -> list[list[Optional[float]]]:
        return query.bands or self.price_bands


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"config not found at {path}; copy config/queries.yml from the repo "
            f"or pass --config"
        )

    raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}

    fetch_raw = raw.get("fetch") or {}
    known = FetchConfig.__dataclass_fields__.keys()
    fetch = FetchConfig(**{k: v for k, v in fetch_raw.items() if k in known})

    queries = []
    for q in raw.get("queries") or []:
        queries.append(QuerySpec(
            id=str(q["id"]),
            keywords=q.get("keywords", "") or "",
            category=str(q["category"]) if q.get("category") else None,
            extra=q.get("extra") or {},
            bands=q.get("price_bands"),
        ))

    if not queries:
        raise ValueError(f"no queries defined in {path}")

    return Config(
        database=raw.get("database", "data/nflcarddb.sqlite"),
        roster=raw.get("roster"),
        inserts=raw.get("inserts"),
        fetch=fetch,
        price_bands=raw.get("price_bands") or list(DEFAULT_BANDS),
        queries=queries,
    )
