"""Leaf adapter — placeholder until implemented (returns nothing)."""
from app.sportsdb.models import Card, Release


class LeafSource:
    name = "leaf"

    async def fetch_releases(self, fetcher) -> list[Release]:
        raise NotImplementedError("Leaf adapter not implemented yet")

    async def fetch_checklist(self, fetcher, release: Release) -> list[Card]:
        return []
