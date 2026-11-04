"""Sports cards via the local scraper DB (app/sportsdb/) — no network, no key.

Registered ahead of CardSight when ``settings.sportsdb_file`` exists and holds
cards. The sync CLI writes the file; a scan must never create it, so a missing
file is simply "no match".
"""
import os
from pathlib import Path
from typing import Optional

from app.catalog.base import Candidate, pick
from app.catalog.cardsight import BASE_SET_NAMES
from app.schemas import CatalogMatch, Identity
from app.sportsdb.db import SportsDb, db_path


class SportsDbSource:
    source = "sportsdb"

    def __init__(self, path: Optional[os.PathLike | str] = None):
        self.path = Path(path) if path else db_path()

    async def lookup(self, identity: Identity) -> Optional[CatalogMatch]:
        if not self.path.exists():
            return None
        year = identity.year.strip() if identity.year else ""
        # sqlite3 connections are thread-bound; one per lookup keeps the
        # adapter safe wherever the loop runs, and the query is a cheap LIKE.
        with SportsDb(self.path) as db:
            rows = db.search_cards(identity.subject, year=int(year) if year.isdigit() else None,
                                   limit=50)
        return pick(self.source, identity, [self._candidate(r) for r in rows])

    @staticmethod
    def _candidate(row) -> Candidate:
        # Same shape as CardSight: the release is the "set", a non-base subset
        # ("Young Guns") is appended so it can be scored against the AI's set.
        subset = row["subset"] or ""
        set_name = (row["release_name"] if subset.lower() in BASE_SET_NAMES or not subset
                    else f"{row['release_name']} {subset}")
        return Candidate(
            catalog_id=f"sportsdb:{row['id']}", name=row["player"], set_name=set_name,
            card_number=row["card_number"], year=str(row["year"]) if row["year"] else None,
            rarity=row["parallel"] or None, image_url=row["image_url"], url=row["release_url"])
