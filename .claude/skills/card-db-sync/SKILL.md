---
name: card-db-sync
description: Run the daily sports card release scraper (Topps, Panini, Upper Deck, Leaf, Onyx, Wild Card) into the local SQLite DB and report what changed. Use when asked to sync/refresh/update the sports card database or run the scraper.
---

# Sports card DB sync

Scrapes the manufacturer sites into `server/data/sportsdb.sqlite` (or
`$SPORTSDB_PATH`) and reports what changed. Nothing leaves the machine; the
scan pipeline reads this file before falling back to CardSight.

## Steps

1. Run the sync from the repo root. Add `--source <name>` only when the user
   named a manufacturer (`topps`, `panini`, `upperdeck`, `leaf`, `onyx`,
   `wildcard`; comma-separated for several):

   ```bash
   cd server && .venv/bin/python -m app.sportsdb sync --json 2>/dev/null
   ```

   It can take a few minutes: the fetcher honours each site's crawl delay.
   Exit code 1 means every source failed; the JSON is still printed.

2. Parse the JSON on stdout:

   ```json
   {"runs": [{"manufacturer": "upperdeck", "status": "ok|blocked|error",
              "releases_seen": 0, "releases_new": 0, "checklists_fetched": 0,
              "cards_new": 0, "error": null, "new_release_names": []}],
    "stats": {"db": "...", "releases": 0, "releases_by_manufacturer": {},
              "releases_by_sport": {}, "cards": 0}}
   ```

3. Report, in this order:
   - One table row per manufacturer: `manufacturer | status | releases_seen |
     releases_new | cards_new`.
   - The new release names (`new_release_names`), grouped by manufacturer.
     Say "no new releases" when every list is empty.
   - Every `blocked` or `error` run, quoting its `error` text. Standing advice:
     Topps and Panini sit behind Cloudflare, so `blocked` there is expected
     with plain HTTP (the adapter module docstring in
     `server/app/sportsdb/sources/<name>.py` explains what was tried). For
     any blocked/error source: rerun later; if it keeps failing, check whether
     the site changed and the adapter needs updating.
   - Totals from `stats`: releases, cards, and the per-manufacturer /
     per-sport breakdowns.

## Follow-up commands

```bash
cd server && .venv/bin/python -m app.sportsdb runs                       # recent runs
cd server && .venv/bin/python -m app.sportsdb search --player "Connor Bedard" --year 2025
cd server && .venv/bin/python -m app.sportsdb stats
```

For automatic daily runs (06:30 local, logs in `server/data/sportsdb-logs/`)
point the user at `server/scripts/install_sportsdb_launchd.sh`
(`--dry-run` prints the plist, `--run-now` kicks it off, `--uninstall`
removes it). Do not install it unless asked.
