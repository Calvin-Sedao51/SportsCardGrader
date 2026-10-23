"""Pokémon via TCGdex (api.tcgdex.net) — free, no key.

The list endpoint returns brief cards (id, localId, name, image) without set
names, so the top few are fetched in detail to score the set.
"""
import asyncio
from typing import Optional

import httpx

from app.catalog.base import Candidate, HttpSource
from app.catalog.match import norm_number
from app.schemas import CatalogMatch, Identity

MAX_DETAILS = 5


class TcgdexSource(HttpSource):
    source = "tcgdex"

    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None):
        super().__init__("https://api.tcgdex.net/v2/en", transport=transport)

    async def lookup(self, identity: Identity) -> Optional[CatalogMatch]:
        resp = await self._http.get("/cards", params={"name": f"eq:{identity.subject}"})
        resp.raise_for_status()
        brief = resp.json() or []
        if not brief:  # "Charizard ex" read as "Charizard": fall back to contains
            resp = await self._http.get("/cards", params={"name": identity.subject})
            resp.raise_for_status()
            brief = resp.json() or []
        want = norm_number(identity.card_number)
        if want:
            # Number-matched printings first; the rest only fill spare slots.
            brief.sort(key=lambda c: norm_number(c.get("localId")) != want)
        details = await asyncio.gather(
            *(self._detail(c["id"]) for c in brief[:MAX_DETAILS]))
        return self.pick(identity, [d for d in details if d is not None])

    async def _detail(self, card_id: str) -> Optional[Candidate]:
        resp = await self._http.get(f"/cards/{card_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        c = resp.json()
        image = c.get("image")
        return Candidate(
            catalog_id=c["id"], name=c.get("name", ""),
            set_name=(c.get("set") or {}).get("name", ""),
            card_number=c.get("localId"), rarity=c.get("rarity"),
            image_url=f"{image}/high.webp" if image else None)
