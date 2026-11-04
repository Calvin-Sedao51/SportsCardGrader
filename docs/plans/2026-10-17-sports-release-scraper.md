# Sports Release Scraper Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or
> subagent-driven-development) to implement this plan task-by-task.

**Goal:** Daily scrape of manufacturer sites into a local SQLite sports card
database that the scan pipeline can identify cards against.

**Architecture:** `server/app/sportsdb/` holds the shared contract
(`models.py`), storage (`db.py`), polite fetcher (`http.py`), orchestration
(`sync.py`) and CLI (`__main__.py`) — **done in commit 7ca2bd6**. One adapter
per manufacturer under `sources/` fills the contract. A catalog adapter
(`app/catalog/sportsdb.py`) queries the DB during scans. A project skill and a
LaunchAgent run the sync daily.

**Tech Stack:** Python 3.12+, httpx, BeautifulSoup4 + lxml, openpyxl,
Playwright (optional, for Cloudflare-fronted sites), stdlib sqlite3, pytest
with `httpx.MockTransport` fixtures. Design: `2026-10-17-sports-release-scraper-design.md`.

---

## Shared contract (read before any task)

```python
# app/sportsdb/models.py
Release(manufacturer, source_id, name, sport, year, release_date, product_type,
        url, image_url, checklist_url, description, raw)
Card(card_number, player, team, subset, parallel, print_run, is_rookie, is_auto, is_relic, raw)
guess_sport(name) -> basketball|hockey|baseball|football|multi|other
guess_year(name) -> int|None

# app/sportsdb/sources/<mfr>.py
class XSource:
    name = "<mfr>"
    async def fetch_releases(self, fetcher: Fetcher) -> list[Release]
    async def fetch_checklist(self, fetcher: Fetcher, release: Release) -> list[Card]

# app/sportsdb/http.py
Fetcher(transport=None, min_delay=2.0, respect_robots=True)
  .get(url) / .get_text / .get_json / .get_bytes      # raises Blocked on 401/403/429/503 or robots
```

Rules for every adapter:
- All HTTP goes through the `fetcher` passed in (politeness lives there).
  Never create your own `httpx` client. A Playwright page is allowed only
  when `fetcher.get` raises `Blocked` for that host.
- `source_id` must be stable across days (product id / slug / URL path).
- `fetch_releases` returns the full current product listing of the trading
  card section (the DB dedupes). Skip supplies, apparel, memorabilia.
- `sport` via `guess_sport(name)` plus any site category hint; `year` via
  `guess_year(name)`; `release_date` ISO if the site shows one.
- `fetch_checklist` returns `[]` when the site has no card list; do not
  scrape third-party sites. Parse xlsx with openpyxl, HTML with bs4. PDFs:
  store `checklist_url`, return `[]`.
- Raise `Blocked` (or let the fetcher's `Blocked` propagate) when the site
  refuses us. Never return invented data.
- Tests: `server/tests/test_sportsdb_<mfr>.py` with saved fixtures in
  `server/tests/fixtures/sportsdb_<mfr>_*.{json,html,xlsx}` via
  `httpx.MockTransport`, plus one `@pytest.mark.live` smoke test (skipped by
  default; run with `pytest -m live`). Keep fixtures small (trim to a few
  products / rows).

## Task 1–6: adapters (independent, parallel-safe)

| Task | File | Recon starting points (2026-10-17) |
|---|---|---|
| 1 Upper Deck | `sources/upperdeck.py` | `https://upperdeck.com/wp-json/wc/store/v1/products?per_page=100&page=N&orderby=date` (public JSON; `categories`, `images`, `permalink`, `short_description`); robots `Crawl-delay: 10`; checklists: `https://upperdeck.com/checklist-sitemap.xml`, `/checklists/`, year/brand/license sitemaps → HTML checklist pages |
| 2 Onyx | `sources/onyx.py` | same WooCommerce Store API on `https://onyxauthenticated.com`; categories are sports (`trading-cards/<sport>`); `product-sitemap.xml`; checklists appear only in product descriptions — extract a checklist link if present, else `[]` |
| 3 Leaf | `sources/leaf.py` | Webflow: `https://www.leaftradingcards.com/sitemap.xml` → `/products/<slug>` (616) and `/shopify-products/<slug>` (109), `/releases` page; product pages link checklists (verify format) |
| 4 Wild Card | `sources/wildcard.py` | `https://wildcardinc.com` React SPA with `/checklists/` route; find the JSON the SPA loads (look at `/assets/App-*.js` for fetch URLs or a sitemap) |
| 5 Topps | `sources/topps.py` | Shopify behind Cloudflare: plain HTTP 403 for everything incl. robots.txt. Try `fetcher.get` first; on `Blocked`, try Playwright Chromium (`playwright` + chromium are installed in `server/.venv`) on `https://www.topps.com/collections/all/products.json?limit=250&page=N` and `https://topps.com/pages/checklists`. If still blocked, the adapter raises `Blocked` with a clear message; document in the module docstring. |
| 6 Panini | `sources/panini.py` | `https://www.paniniamerica.net` returns 403 splash page for plain HTTP. Same Playwright fallback; product listing + `/checklists` (xlsx downloads). Same blocked rule. |

Each adapter task:
1. Recon with `curl`/`python -I` (save sample responses as fixtures).
2. Write failing fixture tests for `fetch_releases` (count, a known release's
   `source_id`/name/sport/year/url) and `fetch_checklist` (known card row).
3. Implement; run `server/.venv/bin/pytest -q tests/test_sportsdb_<mfr>.py`.
4. Live check: `server/.venv/bin/python -m app.sportsdb --db /tmp/<mfr>.sqlite sync --source <mfr>`
   and `stats`; report counts honestly (blocked is a valid outcome).
5. Full suite green: `server/.venv/bin/pytest -q`.

## Task 7: scan integration

- `server/app/catalog/sportsdb.py`: `SportsDbSource(path=None)` with
  `async def lookup(identity) -> Optional[CatalogMatch]`: `db.search_cards(
  identity.subject, year=int(identity.year) if numeric)` → `Candidate(
  catalog_id=f"sportsdb:{card.id}", name=player, set_name=release_name,
  card_number, year=str(year), image_url, url=release_url)` → `self.pick()`.
  Subclass `HttpSource`? No — it has no HTTP. Reuse `Candidate` + the same
  `pick` logic (move `pick` to a mixin or duplicate the 10 lines with the
  same thresholds).
- `schemas.py`: add `"sportsdb"` to `CatalogSourceName`.
- `catalog/__init__.py`: for `sports`, return a `SportsDbSource` when the DB
  file exists and has cards; if a CardSight key is also configured, wrap both
  in a `FirstMatchSource` that tries local then CardSight. Update the module
  docstring table.
- `config.py`: `sportsdb_path: str = ""` (env `SPORTSDB_PATH`, default from
  `app.sportsdb.db.db_path()`).
- Tests in `tests/test_catalog_sportsdb.py` on a temp DB; registration
  tests in `test_catalog_sources.py` style.

## Task 8: daily run

- `.claude/skills/card-db-sync/SKILL.md` (project skill, invoked as
  `/card-db-sync`): runs `server/.venv/bin/python -m app.sportsdb sync --json`,
  then `stats` and `runs`, summarizes new releases / blocked sources, and
  tells the user what to do about blocked ones.
- `server/scripts/install_sportsdb_launchd.sh`: writes
  `~/Library/LaunchAgents/com.cardscanner.sportsdb-sync.plist` (06:30 daily,
  `RunAtLoad` false, logs to `server/data/sportsdb-logs/`), `launchctl
  bootstrap`; `--uninstall` flag. Idempotent.
- README section "Sports card database (local)" documenting CLI, skill,
  launchd, and the blocked-site caveat.
