# Live eBay pricing + card disposition (personal / for sale)

**Date:** 2026-08-22
**Status:** Approved

## Goal

1. Turn on live eBay pricing using the newly granted eBay developer
   (Browse API) production credentials.
2. Let the user mark each scanned card as **personal collection** or
   **for sale**, publicly, with the flag editable both before and after
   the card is published to The Binder on Hive.

## Part 1 — Live eBay pricing (config only)

`server/app/ebay.py` already implements the Browse API end to end:
client-credentials OAuth, category-aware search (sports `212`, CCG
`183454`), price-ascending sampling, and comp statistics feeding the
verdict. No code changes.

Work:

- Create repo-root `.env` with production `EBAY_CLIENT_ID` and
  `EBAY_CLIENT_SECRET` (per `.env.example`). Never commit it.
- Verify end to end: `make dev`, scan a sample card, confirm comps and a
  verdict come back from production eBay.

## Part 2 — Disposition field

### Data model

New field, mirrored in the two places that define the card record:

- `server/app/hive/record.py` — on `CardRecordDraft`:
  `disposition: Literal["personal", "for_sale"] = "personal"`
- `web/src/binderTypes.ts` — same field on `CardRecord`.

The record stays `v: 1`: the field defaults to `"personal"`, so existing
Binder posts without it read back as personal-collection cards.

For-sale cards also get a `forsale` tag on the Hive post
(`post_builder.py`) so the community can browse sellable cards on any
Hive frontend.

### UI

- **ResultsScreen** — two-option toggle after the scan result:
  *Personal collection* (default) / *For sale*. The existing
  asking-price input renders only when *For sale* is selected.
- **HistoryScreen / BinderScreen** — "For sale" badge on flagged cards;
  filter chip on the Binder feed (All / For sale).
- **Editing** — staged (unpublished) cards edit freely in IndexedDB.
  Published cards get an Edit affordance for disposition + asking price,
  calling the update endpoint below.

### Server update path

New endpoint `POST /api/publish/{record_id}/update` accepting the two
mutable fields: `disposition` and `asking_price`.

- Rebuilds body / `json_metadata` / tags via the existing
  `post_builder`.
- Broadcasts a `comment` op with the **same author + permlink** — a
  Hive edit.
- Rides the existing `PublishQueue` as a new `update` job type. Unlike
  creates, retries are safe (same permlink = edit, never a duplicate),
  so the verify-before-retry contract relaxes for this job type.

### Error handling

Follows existing patterns: 503 when Hive is unconfigured, queue retry,
web publish poller surfaces job status.

### Testing

- Server: record round-trip with and without `disposition`;
  `post_builder` emits `forsale` tag only for for-sale cards; update
  route test against a dry-run Hive client; queue `update`-job retry
  semantics.
- Web: `binderTypes` round-trip; ResultsScreen toggle shows/hides
  asking price; Binder filter chip; History badge.

## Out of scope (YAGNI)

No checkout/transactions, no buyer messaging, no sold-price sources, no
eBay listing creation. The flag is a marketplace signal, not a
marketplace.
