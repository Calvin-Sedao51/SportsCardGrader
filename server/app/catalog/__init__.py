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
| sports   | CardSight  | CARDSIGHT_API_KEY (opt-in) |
"""
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
    if category == "sports" and settings.cardsight_configured:
        from app.catalog.cardsight import CardsightSource
        return CardsightSource(settings.cardsight_api_key)
    return None  # other_tcg / other / sports without a key


def apply_match(identity: Identity, match: Optional[CatalogMatch]) -> Identity:
    """Canonical set/number/year from a confident match; subject and variant
    stay the AI's (parallels and the player's name as printed are vision-only).
    A corrected card_number/year is also swapped into search_string — that
    string is what prices the card, now and on every future comps refresh, so
    a verified correction that doesn't reach it would leave the card priced
    against the wrong card forever."""
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
    if "card_number" in update or "year" in update:
        update["search_string"] = _resync_search_string(identity, update)
    return identity.model_copy(update=update)


def _resync_search_string(identity: Identity, update: dict) -> str:
    """Best-effort swap of a corrected card_number/year into the AI's
    free-text search_string, leaving everything else (set phrasing, grade,
    qualifiers) untouched. set_name is too free-form to substring-replace
    safely, so a set-only correction doesn't touch search_string."""
    search_string = identity.search_string
    if "card_number" in update and identity.card_number:
        old_token, new_token = f"#{identity.card_number}", f"#{update['card_number']}"
        if old_token != new_token and old_token in search_string:
            search_string = search_string.replace(old_token, new_token)
    if "year" in update and identity.year and identity.year != update["year"] \
            and identity.year in search_string:
        search_string = search_string.replace(identity.year, update["year"], 1)
    return search_string
