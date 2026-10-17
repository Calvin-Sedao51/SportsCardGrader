"""Upper Deck adapter — placeholder until implemented (returns nothing)."""
from app.sportsdb.models import Card, Release


class UpperDeckSource:
    name = "upperdeck"

    async def fetch_releases(self, fetcher) -> list[Release]:
        raise NotImplementedError("Upper Deck adapter not implemented yet")

    async def fetch_checklist(self, fetcher, release: Release) -> list[Card]:
        return []
