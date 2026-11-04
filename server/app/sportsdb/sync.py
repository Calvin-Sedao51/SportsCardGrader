"""One sync run: for each source, releases -> checklists -> runs row."""
import asyncio
import traceback
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from app.sportsdb.db import SportsDb
from app.sportsdb.http import Blocked, Fetcher
from app.sportsdb.sources import SOURCES, Source, load_source


@dataclass
class RunSummary:
    manufacturer: str
    status: str                 # ok | blocked | error
    releases_seen: int = 0
    releases_new: int = 0
    checklists_fetched: int = 0
    cards_new: int = 0
    error: Optional[str] = None
    new_release_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


Log = Callable[[str], None]


async def sync_source(db: SportsDb, source: Source, fetcher: Fetcher, *,
                      since_days: int = 120, checklists: bool = True,
                      log: Log = lambda _: None) -> RunSummary:
    summary = RunSummary(manufacturer=source.name, status="ok")
    run_id = db.start_run(source.name)
    try:
        releases = await source.fetch_releases(fetcher)
        summary.releases_seen = len(releases)
        ids: dict[str, int] = {}
        for rel in releases:
            rel_id, is_new = db.upsert_release(rel)
            ids[rel.source_id] = rel_id
            if is_new:
                summary.releases_new += 1
                summary.new_release_names.append(rel.name)
        log(f"[{source.name}] {summary.releases_seen} releases, {summary.releases_new} new")
        if checklists:
            # Only recent releases get their checklist refreshed, and only once
            # a day: checklists rarely change after release.
            recent = {r["source_id"]: r for r in db.releases(source.name, since_days=since_days)}
            for rel in releases:
                row = recent.get(rel.source_id)
                if row is None or _synced_today(row["checklist_synced_at"]):
                    continue
                try:
                    cards = await source.fetch_checklist(fetcher, rel)
                except Blocked:
                    raise
                except Exception as e:  # one bad checklist must not sink the run
                    log(f"[{source.name}] checklist failed for {rel.name}: {type(e).__name__}: {e}")
                    continue
                summary.checklists_fetched += 1
                added = db.upsert_cards(ids[rel.source_id], cards)
                summary.cards_new += added
                db.mark_checklist_synced(ids[rel.source_id])
                if cards:
                    log(f"[{source.name}] {rel.name}: {len(cards)} cards ({added} new)")
    except Blocked as e:
        summary.status, summary.error = "blocked", str(e)
        log(f"[{source.name}] BLOCKED: {e}")
    except Exception as e:
        summary.status = "error"
        summary.error = f"{type(e).__name__}: {e}"
        log(f"[{source.name}] ERROR: {summary.error}\n{traceback.format_exc()}")
    db.finish_run(run_id, summary.status, releases_seen=summary.releases_seen,
                  releases_new=summary.releases_new, cards_new=summary.cards_new,
                  error=summary.error)
    return summary


def _synced_today(value: Optional[str]) -> bool:
    from app.sportsdb.db import now_iso
    return bool(value) and value[:10] == now_iso()[:10]


async def sync(db: SportsDb, sources: Optional[list[str]] = None, *,
               since_days: int = 120, checklists: bool = True,
               fetcher: Optional[Fetcher] = None, log: Log = lambda _: None) -> list[RunSummary]:
    """Run every named source concurrently (they hit different hosts)."""
    names = sources or list(SOURCES)
    own_fetcher = fetcher is None
    fetcher = fetcher or Fetcher()
    try:
        return list(await asyncio.gather(*(
            sync_source(db, load_source(n), fetcher, since_days=since_days,
                        checklists=checklists, log=log) for n in names)))
    finally:
        if own_fetcher:
            await fetcher.aclose()
