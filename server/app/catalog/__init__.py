"""Card catalog lookup: verify the vision identity against a real database.

One adapter per category, each with ``async def lookup(identity) ->
Optional[CatalogMatch]``. Lookups are best-effort — the scan pipeline turns
any exception into ``ScanResponse.catalog_error`` and keeps the AI identity.

| category | source     | auth                      |
|----------|------------|---------------------------|
| pokemon  | TCGdex     | none                      |
| magic    | Scryfall   | none                      |
| yugioh   | YGOPRODeck | none                      |
| onepiece | OPTCG API  | none                      |
| sports   | local sportsdb, then CardSight | SPORTSDB_PATH file; CARDSIGHT_API_KEY (opt-in) |
"""
import sqlite3
from typing import Optional, Protocol

from app.catalog.match import norm_text
from app.config import Settings
from app.schemas import CardCategory, CatalogMatch, Identity

# At or above this, the catalog's set/number/year replace the vision guess.
VERIFIED_THRESHOLD = 0.8


class CatalogSource(Protocol):
    async def lookup(self, identity: Identity) -> Optional[CatalogMatch]: ...


def build_catalog_source(category: CardCategory,
                         settings: Settings) -> Optional[CatalogSource]:
    if not settings.catalog_enabled:
        return None
    if category == "pokemon":
        from app.catalog.tcgdex import TcgdexSource
        return TcgdexSource()
    if category == "magic":
        from app.catalog.scryfall import ScryfallSource
        return ScryfallSource()
    if category == "yugioh":
        from app.catalog.ygoprodeck import YgoprodeckSource
        return YgoprodeckSource()
    if category == "onepiece":
        from app.catalog.optcg import OptcgSource
        return OptcgSource()
    if category == "sports":
        sources: list[CatalogSource] = []
        if _sportsdb_has_cards(settings.sportsdb_file):
            from app.catalog.sportsdb import SportsDbSource
            sources.append(SportsDbSource(settings.sportsdb_file))
        if settings.cardsight_configured:
            from app.catalog.cardsight import CardsightSource
            sources.append(CardsightSource(settings.cardsight_api_key))
        if len(sources) > 1:
            return FirstMatchSource(sources)
        return sources[0] if sources else None  # no local DB and no key
    return None  # other_tcg / other


def _sportsdb_has_cards(path) -> bool:
    """A release-only DB (checklists not synced yet) can't identify a card."""
    if not path.exists():
        return False
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT 1 FROM cards LIMIT 1").fetchone() is not None
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()


class FirstMatchSource:
    """Try each source in order; the first match wins. One failing source
    must not hide a later one's answer, so errors only surface when nothing
    else answered."""

    def __init__(self, sources: list[CatalogSource]):
        self.sources = sources

    async def lookup(self, identity: Identity) -> Optional[CatalogMatch]:
        failure: Optional[Exception] = None
        answered = False
        for source in self.sources:
            try:
                match = await source.lookup(identity)
            except Exception as e:
                failure = failure or e
                continue
            if match is not None:
                return match
            answered = True
        if failure is not None and not answered:
            raise failure
        return None


def apply_match(identity: Identity, match: Optional[CatalogMatch]) -> Identity:
    """Canonical set/number/year from a confident match; subject and variant
    stay the AI's (parallels and the player's name as printed are vision-only)."""
    if match is None or match.match_confidence < VERIFIED_THRESHOLD:
        return identity
    update = {}
    canonical, seen = set(norm_text(match.set_name).split()), set(norm_text(identity.set_name).split())
    # TCGdex calls "Scarlet & Violet 151" just "151": keep the AI's richer
    # name when it already contains every word of the canonical one.
    if canonical and not canonical <= seen:
        update["set_name"] = match.set_name
    if match.card_number:
        update["card_number"] = match.card_number
    if match.year:
        update["year"] = match.year
    return identity.model_copy(update=update)
