# Hive Setup — Publishing to The Binder

Card Scanner can publish scanned cards (photos, card details, price comps) as
posts to **The Binder**, a Hive community (`hive-192941`, browsable on
[PeakD](https://peakd.com/c/hive-192941) and [Snapie](https://snapie.io)).
The community feed is the app's storage backend — there is no database.

## How it works

- The web app stages scans locally (IndexedDB). Nothing is published without
  the user explicitly tapping **Publish to The Binder** and confirming the
  public-and-permanent consent dialog.
- The server holds ONE app Hive account's **posting key** and publishes on
  users' behalf. Users do not need Hive accounts.
- Card images upload to `images.hive.blog` (free, permanent CDN), signed with
  the same posting key. Optional fallback: `images.3speak.tv`.
- A file-backed queue (`server/data/publish_queue/`) serializes posts: the
  chain allows **one root post per ~5 minutes** per account. Comps refreshes
  are post *edits* and are not limited by that gap.
- Reads go through Hivemind `bridge` APIs with node failover — the community
  feed IS the card database.

## One-time setup

1. **App account.** Pick or create a Hive account for the app (creating one
   costs ~3 HIVE via any wallet, or use a claimed account token). In `.env`
   at the repo root set:

   ```
   HIVE_ACCOUNT=yourappaccount
   HIVE_POSTING_KEY=5J...           # POSTING key only — never active/owner
   ```

   A leaked posting key can spam-post but cannot touch funds; rotate it from
   any wallet if compromised.

2. **Resource Credits.** Keep ~10–15 HP powered up (or delegated) on the app
   account — enough RC for steady posting. `GET /api/hive/status` shows the
   live RC %; the queue parks itself below `HIVE_MIN_RC_PERCENT` (default 5%).

3. **Subscribe + smoke test.** From `server/`:

   ```
   .venv/bin/python scripts/hive_setup.py              # subscribe + RC check
   HIVE_DRY_RUN=true .venv/bin/python scripts/hive_setup.py --post-test  # rehearse
   .venv/bin/python scripts/hive_setup.py --post-test  # real hello post
   ```

4. **Community props.** The community title/description ("The Binder") can
   only be set by its owner/admin (currently **@meno**) via a `updateProps`
   community custom_json. Ask them to set the title, and optionally to grant
   the app account a `member`/`mod` role so posts never need approval.

## Operational constraints (read before scaling)

- **Single process only.** The publish queue is file-backed and assumes one
  server process. Running replicas risks double-posting; the deterministic
  permlink check is the backstop, not the design.
- **Throughput ceiling:** ~288 cards/day (one root post / 5 min). If The
  Binder outgrows that, switch card records to comments under daily anchor
  posts (comments broadcast every 3 s) — the read path already parses
  `json_metadata.card`, so only the queue and feed fan-out change.
- **Everything published is public, forever.** Posts can be edited but their
  history stays in the chain; images on the CDN cannot be deleted at all.
- **Declined rewards.** Card posts decline payout (`max_accepted_payout` 0)
  so the app account never farms rewards from user content.

## Env reference

| Variable | Default | Meaning |
|---|---|---|
| `HIVE_ACCOUNT` | — | app account that signs posts and image uploads |
| `HIVE_POSTING_KEY` | — | its posting key (never logged) |
| `HIVE_COMMUNITY` | `hive-192941` | target community |
| `HIVE_DRY_RUN` | `false` | log ops instead of broadcasting |
| `HIVE_NODES` | built-in list | comma-separated RPC override |
| `HIVE_QUEUE_DIR` | `data/publish_queue` | job files (gitignored) |
| `HIVE_ROOT_POST_INTERVAL_SECONDS` | `305` | spacing between root posts |
| `HIVE_MIN_RC_PERCENT` | `5.0` | park the queue below this RC |
| `IMAGES_3SPEAK_TOKEN` | — | optional image-host fallback |
| `HIVE_ACCOUNT_MODE` | `shared` | identity model; `per_user` is a dormant shim (see `docs/plans/2026-09-16-binder-identity-v1.md`) |
