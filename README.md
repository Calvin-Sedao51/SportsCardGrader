# Card Scanner

[![CI](https://img.shields.io/badge/CI-GitHub_Actions-blue)](.github/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Point your phone at a trading card — sports or TCG (Pokémon, Yu-Gi-Oh!, Magic, One Piece) — and get an answer: what it is, what shape it's in, and whether the price is right.

- **Identity** — player or character, year, set, card number, parallel/variation.
- **Grade range** — a realistic condition estimate (e.g. "PSA 6–8") from your photo.
- **Authenticity red flags** — warning signs like print pattern anomalies or trimmed edges.
- **Verdict** — priced against live eBay comps: **undervalued**, **fair**, or **overpriced**.

## How it works

```
┌────────────┐    ┌──────────────────┐    ┌───────────────┐    ┌─────────────┐
│ Card photo │ ─▶ │ BYO-AI vision    │ ─▶ │ eBay comps    │ ─▶ │   Verdict   │
│ (phone cam)│    │ (your API key)   │    │ (live search) │    │ under/fair/ │
└────────────┘    │ identity + grade │    │ price stats   │    │ overpriced  │
                  └──────────────────┘    └───────────────┘    └─────────────┘
```

1. You snap the card (front, optionally back) in the web app.
2. The stateless API server forwards the photo to **your** AI provider for identification, grading, and authenticity checks.
3. The server searches eBay for comparable listings and computes price statistics.
4. You get a verdict comparing the listing price you entered against the comps.

## Bring your own AI key

There is no shared backend account and no markup. You paste your own API key in the app's settings:

- Your key is stored **only in your browser** (localStorage) and sent **only to your provider** (relayed per-request by the stateless server, never logged or stored).
- Supported providers: **Anthropic** and **OpenAI**.
- Per-scan cost is billed to you by your provider — typically a few cents per scan.

## Quick start

```bash
git clone https://github.com/YOUR_USER/SportsCardGrader.git
cd SportsCardGrader
make setup                # python venv + server deps, npm install for web
cp .env.example .env      # optional — eBay credentials (see below)
make dev                  # API on :8000, web app on :5173
```

Open http://localhost:5173. The Vite dev server proxies `/api` to `http://localhost:8000` (configured in `web/vite.config.ts`), so no `VITE_API_URL` is needed in development. For production builds, set `VITE_API_URL` to your server's URL (see [DEPLOY.md](DEPLOY.md)).

The API server reads its config from environment variables; `make server` sources the repo-root `.env` automatically before starting uvicorn, so keep your `.env` at the repo root.

Requirements: Python 3.12+, Node 22+.

### eBay setup (optional)

Create a free account at [developer.ebay.com](https://developer.ebay.com), create an app, and put its keys in `.env`:

```
EBAY_CLIENT_ID=your-client-id
EBAY_CLIENT_SECRET=your-client-secret
```

Without them the app still works — scans just won't include market prices or a value verdict.

### Hive publishing — The Binder (optional)

Cards can be published to [The Binder](https://peakd.com/c/hive-192941), a
public Hive community that doubles as the app's storage backend: card photos
go to the free `images.hive.blog` CDN and each card becomes a community post
carrying its full record in `json_metadata`. No database anywhere. Set
`HIVE_ACCOUNT` + `HIVE_POSTING_KEY` in `.env` to enable it — full walkthrough
in [`docs/hive-setup.md`](docs/hive-setup.md). Publishing is per-card opt-in
with an explicit "public and permanent" consent step.

### Plugging in a sold-price source

The free default prices against **active eBay listings** — asking prices, which skew high. If you have access to actual sold data (a price-guide service like PriceCharting/SportsCardsPro or Card Hedge, or your own CardLadder exports), you can swap it in without touching the pipeline: implement the small adapter interface in [`server/app/pricing.py`](server/app/pricing.py) — a `source_type` of `"sold"` plus one async `search(query)` returning comp listings — register it, and set `PRICING_SOURCE=your_source` in `.env`. The verdict wording and the app's captions automatically switch from "asking prices" to "sold prices". See the module docstring in `app/pricing.py` for the three-step walkthrough.

### Sports card database (local scraper)

Sports scans can be checked against a local database of manufacturer
releases and checklists instead of (or before) the quota-limited CardSight
API. A daily scraper pulls the trading-card sections of Topps, Panini, Upper
Deck, Leaf, Onyx and Wild Card into `server/data/sportsdb.sqlite`
(gitignored; override with `SPORTSDB_PATH`). During a scan the local DB is
tried first and CardSight only if it finds nothing and `CARDSIGHT_API_KEY`
is set; with neither, sports scans keep the AI-only identity.

```bash
cd server
.venv/bin/python -m app.sportsdb sync                      # all manufacturers
.venv/bin/python -m app.sportsdb sync --source upperdeck   # one site
.venv/bin/python -m app.sportsdb stats                     # row counts
.venv/bin/python -m app.sportsdb runs                      # recent sync runs
.venv/bin/python -m app.sportsdb search --player "Connor Bedard" --year 2025
```

In Claude Code, `/card-db-sync` runs the sync and summarizes new releases
and blocked sources. For an automatic daily run (06:30 local, logs in
`server/data/sportsdb-logs/`): `server/scripts/install_sportsdb_launchd.sh`
(`--dry-run` to preview, `--run-now`, `--uninstall`).

Caveats: Topps and Panini sit behind Cloudflare and may report `blocked`
rather than data — the adapters never invent rows. The database stays on
your machine until a real backend exists.

## Honest limitations

- **The grade is a range, not a certification.** It's an estimate from a phone photo. Surface flaws, print lines, and edge wear that graders catch under magnification may be invisible in your shot.
- **Authenticity flags are warning signs only** — they are not proof either way. High-value cards deserve professional authentication.
- **v1 prices come from active eBay listings** — asking prices, not sold prices. Asks skew high. (Sold-data API access is pending.)
- **The verdict needs 3+ comparable raw listings**; with fewer, it honestly says "not enough data" instead of guessing.

## Architecture

- **`server/`** — FastAPI. BYO-AI passthrough (your key travels request → provider and is never persisted), eBay Browse API client, verdict logic. No database — optional publishing goes to the Hive blockchain (`app/hive/`, a file-backed queue serializes posts to [The Binder](https://peakd.com/c/hive-192941) community).
- **`web/`** — Vite + React PWA. Camera capture, results, scan history staged in IndexedDB, opt-in publishing to The Binder, and a Binder tab that browses the community feed. Nothing leaves your device except the scan request itself — until you explicitly publish a card.

Design history and decisions: [`docs/plans/`](docs/plans/).

## Testing

```bash
make test
```

Server tests use mocked AI and eBay responses — no credentials or network needed, so CI is green for every contributor out of the box.

Browser automations (sign-up, sign-in, grading, publishing, auth rules) run headed via `playwright-cli`: `bash e2e/run-all.sh`. See [e2e/README.md](e2e/README.md).

## Contributing

Contributions welcome — open an issue or PR. Please keep `make test` green.

## License

[MIT](LICENSE)
