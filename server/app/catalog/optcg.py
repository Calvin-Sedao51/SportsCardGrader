"""One Piece via OPTCG API (optcgapi.com) — free, no key.

Parallel (alt-art) printings share the base card's id; they only win when
vision saw a parallel/alt art.
"""
from typing import Optional

import httpx

from app.catalog.base import Candidate, HttpSource
from app.schemas import CatalogMatch, Identity

PARALLEL_PENALTY = 0.05


class OptcgSource(HttpSource):
    source = "optcg"

    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None):
        super().__init__("https://optcgapi.com/api", transport=transport)

    async def lookup(self, identity: Identity) -> Optional[CatalogMatch]:
        resp = await self._http.get("/sets/filtered/",
                                    params={"card_name": identity.subject})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        rows = resp.json()
        if not isinstance(rows, list):  # error object instead of results
            return None
        variant = (identity.variant or "").lower()
        wants_parallel = "parallel" in variant or "alt" in variant
        candidates = []
        for r in rows:
            is_parallel = "(parallel)" in (r.get("card_name") or "").lower()
            candidates.append(Candidate(
                catalog_id=r.get("card_image_id") or r.get("card_set_id") or "",
                name=r.get("card_name", ""), set_name=r.get("set_name", ""),
                card_number=r.get("card_set_id"), rarity=r.get("rarity"),
                image_url=r.get("card_image"),
                penalty=PARALLEL_PENALTY if is_parallel != wants_parallel else 0.0))
        return self.pick(identity, candidates)
