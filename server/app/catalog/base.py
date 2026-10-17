"""Shared plumbing for catalog adapters: HTTP client and best-candidate pick."""
from dataclasses import dataclass
from typing import Optional

import httpx

from app.catalog.match import score
from app.schemas import CatalogMatch, CatalogSourceName, Identity

# Below this a candidate is noise, not even worth suggesting.
MIN_CONFIDENCE = 0.6
# The catalog must never stall a scan; it runs alongside pricing.
TIMEOUT_SECONDS = 4.0
USER_AGENT = "CardScanner/1.0 (+https://github.com/Calvin-Sedao51/SportsCardGrader)"


@dataclass
class Candidate:
    catalog_id: str
    name: str
    set_name: str
    card_number: Optional[str] = None
    year: Optional[str] = None
    rarity: Optional[str] = None
    image_url: Optional[str] = None
    url: Optional[str] = None
    penalty: float = 0.0  # adapter-specific nudge (e.g. unwanted parallel printing)


class HttpSource:
    source: CatalogSourceName

    def __init__(self, base_url: str, headers: Optional[dict] = None,
                 transport: Optional[httpx.AsyncBaseTransport] = None):
        self._http = httpx.AsyncClient(
            base_url=base_url, transport=transport, timeout=TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                     **(headers or {})})

    def pick(self, identity: Identity, candidates: list[Candidate]) -> Optional[CatalogMatch]:
        best: Optional[tuple[float, Candidate]] = None
        for c in candidates:
            s = score(identity.subject, identity.card_number, identity.set_name,
                      cand_name=c.name, cand_number=c.card_number,
                      cand_set=c.set_name) - c.penalty
            if best is None or s > best[0]:
                best = (s, c)
        if best is None or best[0] < MIN_CONFIDENCE:
            return None
        s, c = best
        return CatalogMatch(
            source=self.source, catalog_id=c.catalog_id, name=c.name,
            set_name=c.set_name, card_number=c.card_number, year=c.year,
            rarity=c.rarity, image_url=c.image_url, url=c.url,
            match_confidence=max(0.0, min(1.0, s)))
