"""Panini adapter — placeholder until implemented (returns nothing)."""
from app.sportsdb.models import Card, Release


class PaniniSource:
    name = "panini"

    async def fetch_releases(self, fetcher) -> list[Release]:
        raise NotImplementedError("Panini adapter not implemented yet")

    async def fetch_checklist(self, fetcher, release: Release) -> list[Card]:
        return []
