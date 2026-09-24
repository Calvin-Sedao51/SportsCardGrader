# The Binder — Identity v1 (Google sign-in + shared-account attribution)

**Status:** implemented on branch `cto-binder-identity` (2026-09-16).
**Decision owner:** Calvin. **Architecture:** advisor-approved 2026-09-16, not
open for reconsideration in v1.

## Goal

A user gets the app, signs in with their Google account, is auto-provisioned
with a "Hive presence", and from then on every card they publish lands in the
public community feed **and** in their own "My Collection". The bring-your-own
AI key flow is unchanged; the no-auth "just scan" path keeps working exactly
as before.

## Flow

```
 ┌──────────┐   Google ID token    ┌────────────────────┐
 │ Web app  │ ───────────────────▶ │ POST /api/auth/    │  verify iss/aud/exp/sub
 │ (PWA)    │                      │      google        │  (signature: Google tokeninfo)
 │          │ ◀─────────────────── │                    │
 │ session: │   app JWT (30d) +    └────────────────────┘
 │ memory + │   user {id, name,              │  users.json (file store)
 │ session- │   hive_display_key}             ▼
 │ Storage  │   Bearer app JWT     ┌────────────────────┐
 │          │ ───────────────────▶ │ POST /api/auth/hive│  idempotent provision:
 │          │ ◀─────────────────── │                    │  presence {mode: shared,
 │          │   Hive presence      └────────────────────┘   display_key: binder-xxxxxxxx}
 │          │
 │ History ▶│   multipart record + Bearer   ┌──────────────────┐   ┌──────────┐
 │ Publish  │ ────────────────────────────▶ │ POST /api/publish│──▶│ publish  │──▶ Hive
 │          │                               │ stamps attribution│   │ queue    │   (ONE app
 │          │                               │ from the token    │   │ (file)   │   account)
 │ Binder ▶ │   GET /api/cards?owner=key    └──────────────────┘   └──────────┘
 │ My Coll. │ ◀──────────────── feed entries carry attribution ◀── json_metadata
 └──────────┘
```

Signed-out users: same screens, Community tab only, publish stays anonymous
(attribution block has null identity), sign-in prompt on My Collection.

## The shared-account attribution architecture

* **One Hive posting account** (`HIVE_ACCOUNT`) signs every post — unchanged
  from the original Binder design (`docs/hive-setup.md`).
* **Identity = Google `sub`**, never exposed. From it the server derives two
  stable identifiers (`server/app/users.py`):
  * `user_id` — internal (`sha256("binder-user:"+sub)[:16]`), carried in the
    app JWT and persisted per publish job.
  * `hive_display_key` — public collector handle, `binder-<8 hex of sub hash>`.
* **Every post carries an attribution block** in `json_metadata` next to the
  card record (`server/app/hive/post_builder.py`):
  `{"attribution": {"user_id", "hive_display_key", "display_name"}}` plus a
  `Collector` row in the markdown body. `record.attribution_from_post()` reads
  it back (falling back to `card.attribution` for pre-identity posts).
* **The server decides attribution, never the client.** `POST /api/publish`
  overwrites the draft's identity fields from the Bearer token's user and
  strips them on anonymous publishes; a bad token is a 401, not a silent
  anonymous post. Email and the raw Google sub never reach the chain.
* **"My Collection" = the shared feed filtered by `hive_display_key`**:
  server-side via `GET /api/cards?owner=<key>` (so paging a busy community
  can't hide older cards) and again client-side (`web/src/auth/collection.ts`).
* **User store** is a JSON file (`server/data/users.json`, atomic
  tmp+rename, same pattern as the publish queue). Single process, like the
  queue. Advisor: review at 100+ users.
* **Session storage on the client**: app JWT lives in memory + sessionStorage,
  never localStorage, so a single-store leak can't yield both the AI billing
  key and the identity token (`web/src/auth/session.ts`).

## Advisor rationale (3 sentences)

Hive allows roughly one root post per five minutes per account and every
account needs its own resource credits, so real per-user accounts would
throttle a 100-user community to uselessness and turn the app into an RC
treasury. One shared posting account already serializes the community's
throughput in a single file-backed queue that is known to work. Attribution
inside the post is enough to give each user "their" collection without the
chain-side cost of per-user accounts.

## Dormant per-user-account shim

* Feature flag: `HIVE_ACCOUNT_MODE` (`server/app/config.py`), default
  `shared`. Startup refuses anything else (`server/app/main.py`) so the
  publish path can never run in a mode it does not honor.
* The single integration point: `hive_presence()` in
  `server/app/hive/identity.py`. The `per_user` branch raises
  `NotImplementedError` and is the only place a future implementation would
  look up/create the user's own account and return it as `posting_account`.
  Nothing else (account creation, wallets, RC management, per-account
  spacing in the queue) exists by decision.
* Client-side, `HivePresence.hive.mode` is already typed
  `'shared' | 'per_user'` so the UI needs no shape change when the flag flips.

## Go / no-go metrics for v1

Collect from `server/data/users.json` + the publish queue directory + the
public feed; no analytics service is involved.

| Metric | How | Go threshold |
|---|---|---|
| Unique scans per user | published jobs grouped by `user_id` (queue files) | median ≥ 3 in the first 30 days |
| RC spend per user | `GET /api/hive/status` `rc_percent` delta per day ÷ active users | stays under the shared account's daily regen at 100 users |
| Sign-in → first-scan conversion | users with ≥ 1 attributed job ÷ users in the store | ≥ 60 % |
| Sign-in drop-off | users in the store with 0 attributed jobs after 7 days | **no-go if > 15 %** of signed-in users never publish |

If RC spend per user or the drop-off threshold fails, the shim above is the
escape hatch to evaluate, not per-user accounts by default.

## What is real vs stubbed

* Real: server auth (stdlib HS256 app JWT; Google signature via the
  tokeninfo endpoint in production), user store, attribution stamping and
  read-back, owner filter, My Collection UI, sync flow, session handling,
  Google Identity Services runtime loader (`VITE_GOOGLE_CLIENT_ID`).
* Stubbed: **Firebase**. It is not in `web/package.json` and could not be
  installed offline. `web/src/auth/firebaseStub.ts` is the documented no-op
  behind the same `googleSignIn(callback)` interface; with no client id the
  app runs fully signed-out. A Firebase-issued Google credential is the same
  Google ID token, so the server needs no change if Firebase is wired in.
* Not built by decision: per-user Hive accounts (see shim).

## Tests

* `server/tests/test_auth.py`, `test_users.py`, `test_auth_endpoints.py`,
  `test_attribution.py` — fake Google JWTs signed with a local HS256 secret,
  fake bridge posts; no network, no real Hive bridge.
* `web/src/auth/*.test.ts(x)`, `binderApi.test.ts`, `BinderScreen.test.tsx`,
  `HistoryScreen.test.tsx` — filter logic, attribution round-trip, tabs,
  sync flow, session storage rules.
