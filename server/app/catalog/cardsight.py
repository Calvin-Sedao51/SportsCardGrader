"""Sports cards via CardSight AI (api.cardsight.ai) — needs CARDSIGHT_API_KEY.

Free tier is 750 calls/month, so this adapter is only registered when a key
is configured; without one, sports scans keep the AI-only identity.
"""
from typing import Optional

import httpx

from app.catalog.base import Candidate, HttpSource
from app.schemas import CatalogMatch, Identity

BASE_SET_NAMES = {"base", "base set"}


class CardsightSource(HttpSource):
    source = "cardsight"

    def __init__(self, api_key: str, transport: Optional[httpx.AsyncBaseTransport] = None):
        super().__init__("https://api.cardsight.ai", headers={"X-API-Key": api_key},
                         transport=transport)

    async def lookup(self, identity: Identity) -> Optional[CatalogMatch]:
        params = {"name": identity.subject, "take": 25}
        if identity.year and identity.year.strip().isdigit():
            params["year"] = identity.year.strip()
        if identity.card_number:
            params["number"] = identity.card_number.lstrip("#").split("/")[0].strip()
        cards = await self._cards(params)
        if not cards and "number" in params:  # misread number: retry without it
            params.pop("number")
            cards = await self._cards(params)
        return self.pick(identity, [self._candidate(c) for c in cards])

    async def _cards(self, params: dict) -> list[dict]:
        resp = await self._http.get("/v1/catalog/cards", params=params)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return resp.json().get("cards", [])

    @staticmethod
    def _candidate(c: dict) -> Candidate:
        # Sports "set" in our identity is the release ("Panini Prizm"); the
        # CardSight set is the subset within it ("Base Set", "Rated Rookies").
        release, subset = c.get("releaseName") or "", c.get("setName") or ""
        set_name = (release if subset.lower() in BASE_SET_NAMES or not subset
                    else f"{release} {subset}".strip())
        return Candidate(
            catalog_id=c["id"], name=c.get("name", ""), set_name=set_name or subset,
            card_number=c.get("number"), year=c.get("releaseYear"))
