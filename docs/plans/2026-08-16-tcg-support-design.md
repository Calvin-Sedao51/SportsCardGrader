# TCG Support (Category-Aware Scan) — Design

**Date:** 2026-08-16
**Status:** Approved (Option B of three; see Alternatives)

## Problem

The app assumes every card is a sports card. Since distribution runs through
Hive — whose user base is international and skews toward trading card games —
Pokémon, Yu-Gi-Oh!, Magic: The Gathering, and One Piece cards will likely be
scanned *more* often than sports cards. Today those scans half-work:

- eBay comps are hardcoded to category **212** (Sports Trading Cards) in both
  `server/app/ebay.py` and the ResultsScreen sold-link, so TCG scans get zero
  or wrong comps — the verdict feature silently breaks.
- The identity field is `player`, and the UI/Binder posts label it "Player" —
  wrong over a Charizard.
- The vision prompt says "sports trading card" and gives only a sports
  search-string example, so the model isn't guided toward the attributes that
  dominate TCG pricing (edition, holo, language, rarity).

Grading is already fine: PSA/CGC/BGS grade TCGs, and the slab enum already
includes CGC and TAG.

## Decision

**Auto-detect the card category from the photo** in the existing vision call —
no extra user input. One new field drives everything downstream.

### Schema (`server/app/schemas.py`)

- `Identity.category: Literal["sports", "pokemon", "yugioh", "magic",
  "onepiece", "other_tcg", "other"]`, default `"sports"` (lenient parsing for
  models that omit it).
- Rename `Identity.player` → `Identity.subject` (the person *or* character/card
  name). Safe now: the Hive record is still `v: 1` and no posts have been
  published to the community.

### Vision prompt (`server/app/vision/prompt.py`)

- Reword to "trading card — sports or a trading card game (Pokémon, Yu-Gi-Oh!,
  Magic: The Gathering, One Piece, etc.)".
- Add `category` to the JSON shape.
- Two `search_string` examples: the existing sports one and a TCG one, e.g.
  `"1999 Pokémon Base Set Charizard #4 Holo 1st Edition"`.
- New rule: for TCGs, put edition/holo/language/rarity in `variant` and in
  `search_string` — they dominate price.
- PSA scale text unchanged (applies to TCGs).

### eBay comps (`server/app/ebay.py`, `web/src/screens/ResultsScreen.tsx`)

- Replace the `SPORTS_CARDS_CATEGORY` constant with a category map:
  `sports → "212"`, all TCG values → `"183454"` (CCG Individual Cards),
  `other → no category filter`.
- The web sold-link builds `_sacat` from the same mapping (kept in sync by
  comment, as today).

### Hive record & posts (`server/app/hive/record.py`, `post_builder.py`,
`web/src/binderTypes.ts`)

- Mirror the schema change field-for-field; record stays `v: 1`.
- Post table row reads "Player" for sports, "Card" otherwise.
- Add the game name (e.g. `pokemon`) to the post's tags for Hive
  discoverability.

### Web UI

- `types.ts` / `binderTypes.ts`: renamed field + `category`.
- Results/History/Binder screens: label "Player" vs "Card" by category;
  Binder search placeholder → "Search card, set, year…".
- README and other copy: "sports card" → "trading card (sports and TCG)".

### Testing

- Update existing tests for the rename.
- New: prompt/parse tests covering `category` (present, absent → default),
  eBay test asserting per-category mapping, post-builder test for a TCG card
  (label + tag).

## Alternatives considered

- **A — Minimal genericization:** reword prompt, drop the eBay category filter.
  Rejected: uncategorized eBay searches pull sleeves/lots/merch; UI still says
  "Player".
- **C — Full TCG data model:** per-game structured schemas (set codes, rarity
  tiers, editions) and TCGplayer API comps. Deferred as YAGNI: the pipeline's
  job is one good eBay search string, which free-text `variant` +
  `search_string` already capture; eBay is the largest TCG sold-comps source.
  The `category` field added here is the seam those features would attach to
  later, without another record migration.
