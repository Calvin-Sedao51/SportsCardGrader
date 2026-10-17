# Sports Card Release Scraper + Local Card Database — Design

**Date:** 2026-10-17
**Status:** Built under stated assumptions (autonomous session); review the
Assumptions section and push back on anything that is wrong.

## Problem

Sports scans only get a real catalog check when `CARDSIGHT_API_KEY` is set
(750 free calls/month). We want our own database of sports card releases
and checklists — basketball, hockey, baseball, football — refreshed daily
from the manufacturers themselves (Topps, Panini, Upper Deck, Leaf, Onyx,
Wild Card), so that when someone uploads a card the scan pipeline can
identify it against local data and fill in year / set / number / player.

No backend exists yet, so the data lives on this machine (SQLite) until a
real store is wired in.

## Recon (2026-10-17, plain HTTPS with a browser user agent)

| Manufacturer | Site | Access | Release data | Card-level checklists |
|---|---|---|---|---|
| Upper Deck | upperdeck.com (WooCommerce) | 200, `Crawl-delay: 10` | `/wp-json/wc/store/v1/products` public JSON | Dedicated `checklist-sitemap.xml` + HTML checklist pages |
| Onyx | onyxauthenticated.com (WooCommerce) | 200 | same Store API, categories per sport | none found yet (product descriptions only) |
| Leaf | leaftradingcards.com (Webflow) | 200 | `sitemap.xml` → `/products/*` (616), `/shopify-products/*` (109), `/releases` | checklist links on product pages (to verify) |
| Wild Card | wildcardinc.com (React SPA) | 200 | SPA; `/checklists/` route, data likely JSON | `/checklists/` (to verify) |
| Topps | topps.com (Shopify) | **403 Cloudflare** for every path incl. robots.txt | `topps.com/pages/checklists` exists | xlsx/pdf downloads behind the same wall |
| Panini | paniniamerica.net | **403** splash page | `/checklists` exists | xlsx downloads behind the same wall |

Topps and Panini need a real browser (Playwright/Chromium) or stay
"blocked"; their adapters record that honestly rather than fake data.

## Decision

**Approach A — one adapter per manufacturer site, shared framework, SQLite.**

### Package `server/app/sportsdb/`

- `models.py` — `Release`, `Card` dataclasses (the adapter contract).
- `db.py` — SQLite schema + upserts (`releases`, `cards`, `runs`), stdlib
  `sqlite3`, DB file `server/data/sportsdb.sqlite` (gitignored), path from
  `SPORTSDB_PATH`.
- `http.py` — `Fetcher`: httpx client, browser UA, per-host politeness
  delay (robots `Crawl-delay` respected, default 2 s), retries, robots.txt
  check via `urllib.robotparser`.
- `sources/<manufacturer>.py` — one adapter each, implementing
  `fetch_releases()` and `fetch_checklist(release)`; unknown/unsupported
  returns `[]`.
- `sync.py` — orchestrates one run: for each source → releases → upsert →
  checklists for releases newer than `--since-days` → upsert → `runs` row.
- `__main__.py` — CLI: `python -m app.sportsdb [--db PATH] sync --source all|topps|…
  --since-days 120 --json` (`--db` precedes the subcommand).

### Data model

`Release`: `manufacturer`, `source_id` (stable per site), `name`, `sport`
(`basketball|hockey|baseball|football|multi|other`), `year` (int|None),
`release_date` (ISO|None), `product_type` (hobby/blaster/… free text),
`url`, `image_url`, `checklist_url`, `description`, `raw` (dict).
Unique on `(manufacturer, source_id)`.

`Card`: `release_id`, `card_number`, `player`, `team`, `subset`,
`parallel`, `print_run` (int|None), `is_rookie`, `is_auto`, `is_relic`,
`raw`. Unique on `(release_id, card_number, subset, parallel)`.

`runs`: one row per (run, source): started/finished, status
(`ok|blocked|error`), counts, error text.

### Scan integration (`server/app/catalog/sportsdb.py`)

`SportsDbSource.lookup(identity)` queries the local `cards` joined to
`releases` by normalized player name + year, scores with the existing
`catalog.match.score`, returns a `CatalogMatch` with `source="sportsdb"`.
Registered for `category == "sports"` when the DB file exists; falls back
to CardSight if it finds nothing and a key is configured.

### Daily run

- Project skill `.claude/skills/card-db-sync/SKILL.md` — invoke
  `/card-db-sync` to run the sync and report what changed.
- `server/scripts/install_sportsdb_launchd.sh` installs a user LaunchAgent
  that runs the CLI every day at 06:30 local, logs to
  `server/data/sportsdb-logs/`.

### Testing

Adapters are tested against saved fixtures (`server/tests/fixtures/sportsdb_*`)
through `httpx.MockTransport`; no network in the test suite. `db.py` and
`sync.py` are tested on a temp SQLite file. One optional live smoke test
per adapter is marked `@pytest.mark.live` and skipped by default.

## Alternatives considered

- **B — Aggregator checklists (Beckett, Cardboard Connection, TCDB):**
  richest card-level data, but third-party ToS/copyright and not what was
  asked. Kept as the documented fallback if Topps/Panini stay blocked.
- **C — Paid API only (CardSight):** already wired, quota-limited, no
  control over coverage. Stays as fallback.

## Assumptions to confirm

1. SQLite on disk is acceptable until a backend exists.
2. Topps/Panini via headless Chromium is acceptable if plain HTTP stays
   blocked; if even that fails, "blocked" status + aggregator fallback later.
3. Sports in scope: basketball, hockey, baseball, football. Other sports
   from the same sites (soccer, golf, track) are stored with `sport=other`.
4. PDF checklists are out of scope for v1 (URL stored, not parsed).
