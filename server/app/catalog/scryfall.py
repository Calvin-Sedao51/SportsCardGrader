"""Magic: The Gathering via Scryfall (api.scryfall.com) — free, no key.

Scryfall asks for a descriptive User-Agent and Accept header (set in base).
"""
from typing import Optional

import httpx

from app.catalog.base import Candidate, HttpSource
from app.schemas import CatalogMatch, Identity

MAX_PRINTS = 50


class ScryfallSource(HttpSource):
    source = "scryfall"

    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None):
        super().__init__("https://api.scryfall.com", transport=transport)

    async def lookup(self, identity: Identity) -> Optional[CatalogMatch]:
        name = identity.subject.replace('"', "")
        cards = await self._search(f'!"{name}"')
        if not cards:  # not an exact card name — let Scryfall's fulltext try
            cards = await self._search(name)
        return self.pick(identity, [self._candidate(c) for c in cards[:MAX_PRINTS]])

    async def _search(self, q: str) -> list[dict]:
        resp = await self._http.get("/cards/search", params={"q": q, "unique": "prints"})
        if resp.status_code == 404:  # Scryfall's "no cards matched"
            return []
        resp.raise_for_status()
        return resp.json().get("data", [])

    @staticmethod
    def _candidate(c: dict) -> Candidate:
        images = c.get("image_uris") or ((c.get("card_faces") or [{}])[0].get("image_uris") or {})
        released = c.get("released_at") or ""
        return Candidate(
            catalog_id=c["id"], name=c.get("name", ""), set_name=c.get("set_name", ""),
            card_number=c.get("collector_number"), year=released[:4] or None,
            rarity=c.get("rarity"), image_url=images.get("normal"),
            url=c.get("scryfall_uri"))
