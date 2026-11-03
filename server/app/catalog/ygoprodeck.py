"""Yu-Gi-Oh! via YGOPRODeck v7 (db.ygoprodeck.com) — free, no key.

One card has many printings; each card_sets entry is its own candidate, with
the set code ('LOB-EN001') as the card number.
"""
from typing import Optional

import httpx

from app.catalog.base import Candidate, HttpSource
from app.schemas import CatalogMatch, Identity

MAX_CARDS = 10


class YgoprodeckSource(HttpSource):
    source = "ygoprodeck"

    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None):
        super().__init__("https://db.ygoprodeck.com/api/v7", transport=transport)

    async def lookup(self, identity: Identity) -> Optional[CatalogMatch]:
        resp = await self._http.get("/cardinfo.php", params={"fname": identity.subject})
        if resp.status_code == 400:  # YGOPRODeck's "no card matching your query"
            return None
        resp.raise_for_status()
        candidates: list[Candidate] = []
        for card in resp.json().get("data", [])[:MAX_CARDS]:
            images = card.get("card_images") or [{}]
            for printing in card.get("card_sets") or [{}]:
                candidates.append(Candidate(
                    catalog_id=str(card["id"]), name=card.get("name", ""),
                    set_name=printing.get("set_name", ""),
                    card_number=printing.get("set_code"),
                    rarity=printing.get("set_rarity"),
                    image_url=images[0].get("image_url"),
                    url=card.get("ygoprodeck_url")))
        return self.pick(identity, candidates)
