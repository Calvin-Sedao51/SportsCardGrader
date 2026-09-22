# TCG Support (Category-Aware Scan) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make trading card games (Pokémon, Yu-Gi-Oh!, Magic, One Piece) first-class alongside sports cards: auto-detected category drives the eBay comps category, identity labels, Hive tags, and copy.

**Architecture:** One new `Identity.category` field (auto-detected by the existing vision call, default `"sports"`) flows through the whole pipeline: schemas → vision prompt → pricing source → eBay category map → Hive record/tags/post → web types/screens. `player` is renamed to `subject` everywhere (safe: Hive record is still v1 and no posts are published).

**Tech Stack:** FastAPI + Pydantic (server), React/TS + Vitest (web), eBay Browse API, Hive (dhive-style broadcast).

**Design doc:** `docs/plans/2026-08-16-tcg-support-design.md`
**Branch:** `feature/hive-binder` (continue on it — the record format lives here).
**Test commands:** server: `cd server && .venv/bin/pytest -q` · web: `cd web && npm test`

**Legacy note (accepted):** staged scans already in a dev browser's IndexedDB carry `identity.player`; after the rename HistoryScreen uses a read fallback, and re-publishing an old draft may fail validation. Pre-launch dev data — rescan if needed. No migration code (YAGNI).

---

### Task 1: `CardCategory` + `subject` rename in server schemas

**Files:**
- Modify: `server/app/schemas.py`
- Test: `server/tests/test_schemas.py`

**Step 1: Write the failing tests** — append to `server/tests/test_schemas.py`:

```python
def test_identity_category_defaults_to_sports():
    i = Identity(subject="Luka Doncic", year="2018", set_name="Prizm",
                 search_string="x", confidence=0.9)
    assert i.category == "sports"

def test_identity_accepts_tcg_category():
    i = Identity(subject="Charizard", year="1999", set_name="Base Set",
                 category="pokemon", search_string="x", confidence=0.9)
    assert i.category == "pokemon"

def test_identity_rejects_unknown_category():
    with pytest.raises(ValidationError):
        Identity(subject="Charizard", year="1999", set_name="Base Set",
                 category="beanie_babies", search_string="x", confidence=0.9)
```

**Step 2: Run to verify they fail**

Run: `cd server && .venv/bin/pytest tests/test_schemas.py -q`
Expected: FAIL — `Identity` has no field `subject`/`category` (extra fields forbidden or unexpected keyword).

**Step 3: Implement** — in `server/app/schemas.py` replace the `Identity` class:

```python
CardCategory = Literal["sports", "pokemon", "yugioh", "magic", "onepiece",
                       "other_tcg", "other"]

class Identity(BaseModel):
    subject: str  # player (sports) or card/character name (TCG)
    category: CardCategory = "sports"  # lenient default for models that omit it
    year: str
    set_name: str
    card_number: Optional[str] = None
    variant: Optional[str] = None
    search_string: str
    confidence: float = Field(ge=0, le=1)
```

**Step 4: Mechanical rename across the server** — every `player` reference becomes `subject` (field name and JSON keys). Exact sites (from grep; comment prose in `comps.py:19` about "player names" in junk-listing titles stays as-is):

- `server/tests/test_schemas.py` (existing `test_identity_confidence_bounds`)
- `server/tests/test_pricing.py:16`, `server/tests/test_scan_endpoint.py:18,28,61`
- `server/tests/test_vision_prompt.py:23,30,71` (JSON strings: `"player":` → `"subject":`)
- `server/tests/test_hive_record.py:45`, `server/tests/test_integration.py:108`
- `server/tests/fixtures/vision_luka_good.json:5` (`"player"` key → `"subject"`; also add `"category": "sports"` while here)
- `server/app/hive/post_builder.py:22,54` and `server/app/hive/record.py:79,91` (attribute access only in this task; label/tag behavior changes come in Tasks 5–6)
- `server/app/vision/prompt.py:35` (JSON shape text; full prompt rewrite is Task 2)

`test_comps.py:56` ("pick your player" listing title) is data, not a field — leave it.

**Step 5: Run the whole server suite**

Run: `cd server && .venv/bin/pytest -q`
Expected: PASS (all — the rename is complete when nothing references `player`). Verify: `grep -rn '\bplayer\b' server/app server/tests --include='*.py' --include='*.json' | grep -v comps` returns only the `test_comps.py` listing-title line.

**Step 6: Commit**

```bash
git add server
git commit -m "feat(server): Identity.category + player->subject rename"
```

---

### Task 2: Vision prompt — trading-card wording, category, TCG guidance

**Files:**
- Modify: `server/app/vision/prompt.py`
- Test: `server/tests/test_vision_prompt.py`

**Step 1: Write the failing tests** — append:

```python
def test_prompt_covers_tcg():
    p = build_prompt()
    assert "Pokémon" in p and "category" in p and "1st Edition" in p


def test_parse_category_round_trips():
    raw = ('{"photo_ok": true, "identity": {"subject": "Charizard", "category": "pokemon", '
           '"year": "1999", "set_name": "Base Set", "card_number": "4", '
           '"variant": "Holo 1st Edition", '
           '"search_string": "1999 Pokemon Base Set Charizard #4 Holo 1st Edition", '
           '"confidence": 0.9}, "condition": {"observations": [], '
           '"grade_low": 5, "grade_high": 7}, '
           '"authenticity": {"red_flags": [], "risk": "low"}, "ai_value_note": null}')
    r = parse_vision_json(raw)
    assert r.identity.category == "pokemon"
    assert r.identity.subject == "Charizard"


def test_parse_missing_category_defaults_to_sports():
    raw = ('{"photo_ok": true, "identity": {"subject": "Luka Doncic", "year": "2018", '
           '"set_name": "Panini Prizm", "card_number": "280", "variant": null, '
           '"search_string": "2018 Panini Prizm Luka Doncic #280", "confidence": 0.92}, '
           '"condition": {"observations": [], "grade_low": 6, "grade_high": 8}, '
           '"authenticity": {"red_flags": [], "risk": "low"}, "ai_value_note": null}')
    assert parse_vision_json(raw).identity.category == "sports"
```

**Step 2: Run to verify the prompt test fails**

Run: `cd server && .venv/bin/pytest tests/test_vision_prompt.py -q`
Expected: `test_prompt_covers_tcg` FAILS ("Pokémon" not in prompt). The two parse tests may already pass from Task 1 — that's fine; they pin the contract.

**Step 3: Rewrite `build_prompt`** — replace the returned f-string body with:

```python
    return f"""You are analyzing photos of a trading card — a sports card or a trading card game
(TCG) card such as Pokémon, Yu-Gi-Oh!, Magic: The Gathering, or One Piece — for a collector
deciding whether to buy it.

Respond with ONLY a JSON object, no prose, matching exactly this shape:
{{
  "photo_ok": bool,            // false if too blurry/glared/cropped to judge
  "photo_issue": str|null,     // if photo_ok is false: what to fix when retaking
  "identity": {{"subject": str,  // player name (sports) or card/character name (TCG)
               "category": "sports"|"pokemon"|"yugioh"|"magic"|"onepiece"|"other_tcg"|"other",
               "year": str, "set_name": str, "card_number": str|null,
               "variant": str|null, "search_string": str, "confidence": float 0-1}} | null,
  "condition": {{"observations": [{{"area": "corners"|"edges"|"surface"|"centering",
                "severity": "none"|"minor"|"moderate"|"heavy", "note": str}}],
                "grade_low": number 1-10, "grade_high": number 1-10}} | null,
  "slab": {{"company": "PSA"|"BGS"|"SGC"|"CGC"|"TAG", "grade": str}} | null,
                               // ONLY if the card is in a professional grading holder — read the label
  "authenticity": {{"red_flags": [str], "risk": "low"|"caution"|"high"}} | null,
  "ai_value_note": str|null    // rough market value from your knowledge, one sentence, or null
}}

Rules:
- "search_string" must be a normalized eBay search, e.g.
  sports: "2018 Panini Prizm Luka Doncic #280 Silver"
  TCG:    "1999 Pokémon Base Set Charizard #4 Holo 1st Edition"
- For TCG cards, put edition (1st Edition/Unlimited), holo/foil, language, and rarity in
  "variant" AND in "search_string" — these dominate TCG pricing.
- Grade as a RANGE; half grades like 6.5 or 8.5 are allowed. A phone photo cannot distinguish
  PSA 9 from 10 — be honest about the spread.
- Authenticity red_flags are warning signs (print dot pattern, era-inconsistent fonts/logos,
  gloss, miscut suggesting a reprint sheet; for TCGs also missing texture/foil pattern or
  wrong card-back shade), NOT a certification.
- If the card is slabbed: read company and grade from the label; include the company and
  grade in search_string (e.g. "2018 Panini Prizm Luka Doncic #280 PSA 9"); set condition
  to null (the slab already graded it); authenticity red flags should consider fake-slab
  signs (label font, hologram).
- If photo_ok is false, set identity/condition/authenticity to null.

PSA grading scale for reference (applies to sports and TCG cards alike):
{scale}"""
```

**Step 4: Run tests**

Run: `cd server && .venv/bin/pytest tests/test_vision_prompt.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add server/app/vision/prompt.py server/tests/test_vision_prompt.py
git commit -m "feat(server): vision prompt covers TCG cards and category detection"
```

---

### Task 3: eBay category map

**Files:**
- Modify: `server/app/ebay.py`
- Test: `server/tests/test_ebay.py`

**Step 1: Write the failing tests** — in `test_ebay.py`, extend `make_transport`'s search handler to capture params, and add:

```python
def make_transport(token_calls: list | None = None, extra_items: list | None = None,
                   seen_params: list | None = None):
    # ... existing handler; inside the search branch add:
    #     if seen_params is not None:
    #         seen_params.append(dict(request.url.params))


async def test_search_sports_uses_sports_category():
    seen: list = []
    client = EbayClient("id", "secret", "production",
                        transport=make_transport(seen_params=seen))
    await client.search("2018 Prizm Luka Doncic", category="sports")
    assert seen[0]["category_ids"] == "212"


async def test_search_tcg_uses_ccg_category():
    seen: list = []
    client = EbayClient("id", "secret", "production",
                        transport=make_transport(seen_params=seen))
    await client.search("1999 Pokemon Base Set Charizard", category="pokemon")
    assert seen[0]["category_ids"] == "183454"


async def test_search_other_omits_category_filter():
    seen: list = []
    client = EbayClient("id", "secret", "production",
                        transport=make_transport(seen_params=seen))
    await client.search("mystery card", category="other")
    assert "category_ids" not in seen[0]
```

**Step 2: Run to verify they fail**

Run: `cd server && .venv/bin/pytest tests/test_ebay.py -q`
Expected: FAIL — `search() got an unexpected keyword argument 'category'`.

**Step 3: Implement** — in `server/app/ebay.py` replace the constant and signature:

```python
from app.schemas import CardCategory, CompListing

# eBay Browse category ids. 212 = Sports Trading Cards; 183454 = CCG Individual
# Cards (Pokémon, Yu-Gi-Oh!, MTG, One Piece all live here). "other" gets no
# filter. Keep in sync with SACAT_BY_CATEGORY in web/src/screens/ResultsScreen.tsx.
EBAY_CATEGORY: dict[str, str] = {
    "sports": "212",
    "pokemon": "183454", "yugioh": "183454", "magic": "183454",
    "onepiece": "183454", "other_tcg": "183454",
}

    async def search(self, query: str, category: CardCategory = "sports",
                     limit: int = 200) -> list[CompListing]:
        ...
        params = {"q": query, "limit": limit, "sort": "price"}
        category_id = EBAY_CATEGORY.get(category)
        if category_id:
            params["category_ids"] = category_id
        resp = await self._http.get(..., params=params)
```

(Keep the existing comments about limit/sort; delete `SPORTS_CARDS_CATEGORY`.)

**Step 4: Run tests**

Run: `cd server && .venv/bin/pytest tests/test_ebay.py -q`
Expected: PASS (existing tests still pass — default category is sports → 212).

**Step 5: Commit**

```bash
git add server/app/ebay.py server/tests/test_ebay.py
git commit -m "feat(server): eBay comps search picks category from card type"
```

---

### Task 4: Plumb category through pricing → scan pipeline

**Files:**
- Modify: `server/app/pricing.py`, `server/app/scan.py`
- Test: `server/tests/test_scan_endpoint.py`

**Step 1: Write the failing test** — append to `test_scan_endpoint.py`:

```python
POKEMON_VISION = VisionResult(
    photo_ok=True,
    identity=Identity(subject="Charizard", category="pokemon", year="1999",
                      set_name="Base Set", card_number="4", variant="Holo 1st Edition",
                      search_string="1999 Pokemon Base Set Charizard #4 Holo 1st Edition",
                      confidence=0.9),
    condition=Condition(observations=[], grade_low=5, grade_high=7),
    authenticity=Authenticity(red_flags=[], risk="low"),
)


def test_scan_passes_category_to_pricing(client, monkeypatch):
    seen: list = []
    async def fake_vision(*a, **k): return POKEMON_VISION
    async def fake_search(q, category="sports"):
        seen.append(category)
        return LISTINGS
    monkeypatch.setattr(scan_module, "run_vision", fake_vision)
    monkeypatch.setattr(scan_module, "search_comps", fake_search)
    assert post_scan(client, asking_price="30").status_code == 200
    assert seen == ["pokemon"]
```

**Step 2: Run to verify it fails**

Run: `cd server && .venv/bin/pytest tests/test_scan_endpoint.py -q`
Expected: new test FAILS (`seen == ["sports"]` or a TypeError — category never passed).

**Step 3: Implement**

- `server/app/pricing.py` — protocol and adapter take the category (update the module docstring's step-1 instructions to mention the new argument):

```python
from app.schemas import CardCategory, CompListing

class PricingSource(Protocol):
    source_type: Literal["active_listings", "sold"]

    async def search(self, query: str,
                     category: CardCategory = "sports") -> list[CompListing]: ...

# EbayActiveSource.search grows the same parameter and forwards it:
    async def search(self, query: str,
                     category: CardCategory = "sports") -> list[CompListing]:
        ...
        return await self._client.search(query, category=category)
```

- `server/app/scan.py`:

```python
async def search_comps(query: str, category: CardCategory = "sports") -> list[CompListing]:
    return await _get_pricing_source().search(query, category=category)

# in price_vision:
        listings = await search_comps(vision.identity.search_string,
                                      vision.identity.category)
```

**Step 4: Fix existing fakes** — the suite monkeypatches `search_comps` with `async def fake_search(q)`; give every such fake the signature `async def fake_search(q, category="sports")` (grep: `fake_search` in `server/tests/`).

**Step 5: Run the server suite**

Run: `cd server && .venv/bin/pytest -q`
Expected: PASS.

**Step 6: Commit**

```bash
git add server/app/pricing.py server/app/scan.py server/tests
git commit -m "feat(server): thread card category from vision to comps search"
```

---

### Task 5: Hive tags + Binder post label

**Files:**
- Modify: `server/app/hive/record.py`, `server/app/hive/post_builder.py`
- Test: `server/tests/test_hive_record.py`

**Step 1: Write the failing tests** — append to `test_hive_record.py` (reuse the file's existing record-building helper/fixture; shown here with a plain Identity):

```python
def test_tags_use_game_name_for_tcg():
    identity = Identity(subject="Charizard", category="pokemon", year="1999",
                        set_name="Base Set", search_string="x", confidence=0.9)
    tags = build_tags(identity, "hive-192941")
    assert tags[0] == "hive-192941"
    assert "pokemon" in tags and "sportscards" not in tags


def test_tags_keep_sportscards_for_sports():
    identity = Identity(subject="Luka Doncic", year="2018",
                        set_name="Panini Prizm", search_string="x", confidence=0.9)
    assert "sportscards" in build_tags(identity, "hive-192941")


def test_post_body_labels_tcg_row_card():
    # Build a record whose identity has category="pokemon" (reuse the module's
    # record factory), then:
    ops = build_post(record, community="hive-192941", account="app", permlink="p")
    body = ops[0][1]["body"]
    assert "| Card | Charizard |" in body
    assert "| Player |" not in body
```

**Step 2: Run to verify they fail**

Run: `cd server && .venv/bin/pytest tests/test_hive_record.py -q`
Expected: FAIL — tags contain `sportscards` for pokemon; body says `| Player |`.

**Step 3: Implement**

- `server/app/hive/record.py` — category-aware base tag (real Hive tags: `pokemon`, `yugioh`, `mtg`, `onepiece` are live communities/topics — this is the discoverability win on Hive):

```python
GAME_TAG = {"sports": "sportscards", "pokemon": "pokemon", "yugioh": "yugioh",
            "magic": "mtg", "onepiece": "onepiece",
            "other_tcg": "tradingcards", "other": "tradingcards"}


def build_tags(identity: Identity, community: str) -> list[str]:
    """First tag MUST be the community (that's what assigns the post to it).

    Derived tags are sc- prefixed so they always start with a letter and never
    collide with organic tags. Hive tags must match [a-z][a-z0-9-]*.
    """
    tags = [community, GAME_TAG[identity.category], "cardscanner"]
    for raw, cap in ((identity.subject, 32), (identity.year, 12), (identity.set_name, 24)):
        ...  # unchanged
```

- `server/app/hive/post_builder.py` — in `_body`, replace the Player row:

```python
    subject_label = "Player" if identity.category == "sports" else "Card"
    lines += ["| | |", "|---|---|",
              f"| {subject_label} | {identity.subject} |",
              ...]
```

**Step 4: Run tests**

Run: `cd server && .venv/bin/pytest tests/test_hive_record.py tests/test_publish_endpoints.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add server/app/hive server/tests/test_hive_record.py
git commit -m "feat(server): game-name Hive tags and Card label for TCG posts"
```

---

### Task 6: Web — types, eBay sold link, labels, copy

**Files:**
- Modify: `web/src/types.ts`, `web/src/screens/ResultsScreen.tsx`, `web/src/screens/HistoryScreen.tsx`, `web/src/screens/BinderScreen.tsx`
- Test: `web/src/screens/ResultsScreen.test.tsx` (+ mechanical fixture updates in the other `.test.tsx` / `binderApi.test.ts`)

**Step 1: Update types** — `web/src/types.ts` (mirrors `schemas.py`):

```typescript
export type CardCategory =
  | 'sports' | 'pokemon' | 'yugioh' | 'magic' | 'onepiece' | 'other_tcg' | 'other'

export interface Identity {
  subject: string // player (sports) or card/character name (TCG)
  category?: CardCategory // optional: staged scans from before this field lack it
  year: string
  ...
}
```

**Step 2: Write the failing test** — in `ResultsScreen.test.tsx`, rename the fixture's `player:` to `subject:`, add `category: 'sports'`, and add a Pokémon case:

```tsx
it('links sold comps to the CCG category for TCG cards', () => {
  // clone the base result, set identity.category = 'pokemon',
  // identity.subject = 'Charizard', search_string accordingly; render; then:
  expect(screen.getByRole('link', { name: /sold comps/i })).toHaveAttribute(
    'href',
    expect.stringContaining('_sacat=183454'),
  )
})
```

**Step 3: Run to verify it fails**

Run: `cd web && npm test`
Expected: FAIL — TS errors on `player` plus the new assertion (`_sacat=212` hardcoded).

**Step 4: Implement**

- `ResultsScreen.tsx`:

```tsx
// Keep in sync with EBAY_CATEGORY in server/app/ebay.py.
const SACAT_BY_CATEGORY: Partial<Record<CardCategory, string>> = {
  sports: '212',
  pokemon: '183454', yugioh: '183454', magic: '183454',
  onepiece: '183454', other_tcg: '183454',
}

function SoldCompsLink({ searchString, category }: { searchString: string; category?: CardCategory }) {
  const sacat = SACAT_BY_CATEGORY[category ?? 'sports']
  const href =
    `https://www.ebay.com/sch/i.html?_nkw=${encodeURIComponent(searchString)}` +
    (sacat ? `&_sacat=${sacat}` : '') +
    '&LH_Sold=1&LH_Complete=1'
  ...
}
// call site passes category={identity.category}; <h2>{identity.subject}</h2>
```

- `HistoryScreen.tsx:82` — rename with a fallback for pre-rename staged scans:

```tsx
{card.response.vision.identity?.subject ??
  (card.response.vision.identity as { player?: string } | null)?.player ??
  'Unreadable photo'}
```

- `BinderScreen.tsx` — `haystack` and the card row use `identity.subject`; placeholder → `"Search card, set, year…"` (BinderScreen renders server-published records only — no legacy fallback needed).
- Mechanical fixture rename (`player:` → `subject:`) in `BinderScreen.test.tsx`, `HistoryScreen.test.tsx`, `binderApi.test.ts`; local variable names in `BinderScreen.test.tsx` may stay.

**Step 5: Run the web suite**

Run: `cd web && npm test`
Expected: PASS. Verify no stragglers: `grep -rn '\bplayer\b' web/src --include='*.ts' --include='*.tsx'` → only the HistoryScreen fallback cast and CSS class names (`className="player"` may stay — cosmetic).

**Step 6: Commit**

```bash
git add web/src
git commit -m "feat(web): category-aware labels and eBay sold link, subject rename"
```

---

### Task 7: Copy sweep + full verification

**Files:**
- Modify: `README.md` (and any other "sports card" copy: `grep -rni 'sports card' README.md DEPLOY.md docs web/index.html web/src server/app --include='*.md' --include='*.html' --include='*.py' --include='*.tsx'`)

**Step 1: Update copy**

- `README.md` line 6: "Point your phone at a sports card" → "Point your phone at a trading card — sports or TCG (Pokémon, Yu-Gi-Oh!, Magic, One Piece) —".
- Identity bullet: "player, year, set" → "player or character, year, set".
- Fix any other hits the grep finds (skip `docs/plans/` history and this plan).

**Step 2: Full test run**

Run: `make test`
Expected: both suites PASS.

**Step 3: Manual smoke (optional but recommended)** — `make dev`, scan a Pokémon card from `sample_images` (or any phone photo): identity shows the card name, sold-comps link lands in eBay's CCG category, and a dry-run publish body shows `| Card |` and a `pokemon` tag.

**Step 4: Commit**

```bash
git add README.md DEPLOY.md docs web server
git commit -m "docs: copy covers TCG cards"
```
