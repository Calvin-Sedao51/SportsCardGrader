"""CLI: python -m app.sportsdb [--db PATH] sync|stats|search|runs

  --db PATH goes before the subcommand (default: $SPORTSDB_PATH or
  server/data/sportsdb.sqlite).

  sync   [--source all|topps,...] [--since-days N] [--no-checklists] [--json]
  stats
  search --player "Connor Bedard" [--year 2023]
  runs
"""
import argparse
import asyncio
import json
import sys

from app.sportsdb.db import SportsDb
from app.sportsdb.sources import SOURCES
from app.sportsdb.sync import sync


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.sportsdb")
    p.add_argument("--db", help="SQLite path (default: $SPORTSDB_PATH or server/data/sportsdb.sqlite)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync", help="scrape manufacturer sites into the DB")
    s.add_argument("--source", default="all", help="comma list of " + ",".join(SOURCES) + " or all")
    s.add_argument("--since-days", type=int, default=120,
                   help="refresh checklists for releases newer than this")
    s.add_argument("--no-checklists", action="store_true", help="releases only")
    s.add_argument("--json", action="store_true", help="print a JSON summary (for the daily log)")
    sub.add_parser("stats", help="row counts")
    sub.add_parser("runs", help="recent sync runs")
    q = sub.add_parser("search", help="look a player up in the local cards table")
    q.add_argument("--player", required=True)
    q.add_argument("--year", type=int)
    return p


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    with SportsDb(args.db) as db:
        if args.cmd == "sync":
            names = list(SOURCES) if args.source == "all" else args.source.split(",")
            # Progress and per-checklist failures always go to stderr; --json
            # only changes what stdout carries (the launchd job keeps both).
            log = lambda m: print(m, file=sys.stderr, flush=True)
            summaries = asyncio.run(sync(db, names, since_days=args.since_days,
                                         checklists=not args.no_checklists, log=log))
            if args.json:
                print(json.dumps({"runs": [s.to_dict() for s in summaries],
                                  "stats": db.stats()}, indent=2))
            else:
                for s in summaries:
                    print(f"{s.manufacturer:10} {s.status:8} releases={s.releases_seen} "
                          f"new={s.releases_new} cards_new={s.cards_new}"
                          + (f"  {s.error}" if s.error else ""))
            return 1 if all(s.status != "ok" for s in summaries) else 0
        if args.cmd == "stats":
            print(json.dumps(db.stats(), indent=2))
        elif args.cmd == "runs":
            for r in db.last_runs():
                print(f"{r['started_at']} {r['manufacturer']:10} {r['status']:8} "
                      f"seen={r['releases_seen']} new={r['releases_new']} cards={r['cards_new']}"
                      + (f"  {r['error']}" if r["error"] else ""))
        elif args.cmd == "search":
            for c in db.search_cards(args.player, args.year):
                print(f"{c['year']} {c['release_name']} #{c['card_number']} {c['player']}"
                      f"{' ' + c['subset'] if c['subset'] else ''}"
                      f"{' [' + c['parallel'] + ']' if c['parallel'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
