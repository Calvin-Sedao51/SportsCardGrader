"""Registry of manufacturer adapters.

Each adapter module exposes one class with::

    name = "upperdeck"                       # registry key
    async def fetch_releases(self, fetcher) -> list[Release]
    async def fetch_checklist(self, fetcher, release) -> list[Card]

``fetch_releases`` returns the current/new product listing (newest first is
fine; the DB dedupes by ``source_id``). ``fetch_checklist`` returns ``[]`` when
the site has no card list for that release. Raise ``app.sportsdb.http.Blocked``
when the site refuses us so the run is recorded as blocked, not errored.
"""
import importlib
from typing import Protocol

from app.sportsdb.models import Card, Release

SOURCES: dict[str, str] = {
    "topps": "app.sportsdb.sources.topps:ToppsSource",
    "panini": "app.sportsdb.sources.panini:PaniniSource",
    "upperdeck": "app.sportsdb.sources.upperdeck:UpperDeckSource",
    "leaf": "app.sportsdb.sources.leaf:LeafSource",
    "onyx": "app.sportsdb.sources.onyx:OnyxSource",
    "wildcard": "app.sportsdb.sources.wildcard:WildCardSource",
}


class Source(Protocol):
    name: str

    async def fetch_releases(self, fetcher) -> list[Release]: ...

    async def fetch_checklist(self, fetcher, release: Release) -> list[Card]: ...


def load_source(name: str) -> Source:
    if name not in SOURCES:
        raise KeyError(f"unknown source {name!r}; known: {', '.join(SOURCES)}")
    module_name, _, cls_name = SOURCES[name].partition(":")
    return getattr(importlib.import_module(module_name), cls_name)()
