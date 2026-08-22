# Live eBay Pricing + Card Disposition Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn on live eBay Browse pricing with the new production credentials, and add a public `disposition` field (`personal` | `for_sale`) to the card record end-to-end, editable before and after Hive publishing.

**Architecture:** The eBay client (`server/app/ebay.py`) is finished — Part 1 is `.env` config plus a live smoke test. Part 2 adds one field to the canonical card record (mirrored server/web), a `forsale` Hive tag, a listing-update endpoint that follows the existing `refresh-comps` fetch→patch→enqueue pattern, and UI in Results/History/Binder. It also fixes a real pre-existing bug: `PublishQueue.tick()`'s on-chain idempotency check short-circuits **every** `update` job (the post always already exists), so edits confirm without ever broadcasting.

**Tech Stack:** FastAPI + Pydantic v2 + pytest (server), React + TypeScript + Vitest/Testing Library (web), Hive via lighthive.

**Design doc:** `docs/plans/2026-08-22-ebay-live-pricing-and-disposition-design.md`

**Conventions:** Server tests run with `cd server && .venv/bin/pytest -q`, web tests with `cd web && npm test`. Commit after every green task. Keep `server/app/hive/record.py` and `web/src/binderTypes.ts` field-for-field identical — both files say so in their headers.

---

### Task 1: Live eBay credentials

**Files:**
- Create: `.env` (repo root — gitignored, NEVER commit)

**Step 1: Create `.env` from the example**

```bash
cp .env.example .env
```

Fill in the production keyset from https://developer.ebay.com → My Account → Keys:

```
EBAY_CLIENT_ID=<App ID (Client ID)>
EBAY_CLIENT_SECRET=<Cert ID (Client Secret)>
EBAY_ENV=production
```

Leave the Hive vars as they were (or copy them from wherever the current deployment keeps them).

**Step 2: Verify the credentials work without the UI**

```bash
cd server && set -a && source ../.env && set +a && .venv/bin/python -c "
import asyncio
from app.ebay import EbayClient
import os
async def main():
    c = EbayClient(os.environ['EBAY_CLIENT_ID'], os.environ['EBAY_CLIENT_SECRET'])
    comps = await c.search('2018 Prizm Luka Doncic rookie', category='sports', limit=10)
    print(len(comps), 'comps;', comps[0].title if comps else 'none')
asyncio.run(main())
"
```

Expected: a line like `10 comps; 2018 Panini Prizm Luka Doncic ...`. A 401/invalid_client here means the keys are wrong or sandbox-only; fix before continuing.

**Step 3: End-to-end smoke test**

Run `make dev`, open http://localhost:5173, scan a card from `sample_images/`, confirm the Results screen shows a verdict with "based on current eBay asking prices".

**Step 4: No commit** — this task changes no tracked files.

---

### Task 2: Server — `disposition` on the card record

**Files:**
- Modify: `server/app/hive/record.py` (CardRecordDraft, ~line 52)
- Test: `server/tests/test_hive_record.py`

**Step 1: Write the failing tests** (append to `server/tests/test_hive_record.py`, matching its existing `make_record` helper style):

```python
def test_disposition_defaults_to_personal():
    # Existing v1 posts on chain have no disposition field — they must
    # validate and read back as personal-collection cards.
    record = make_record()
    data = json.loads(record.model_dump_json())
    data.pop("disposition", None)
    assert CardRecord.model_validate(data).disposition == "personal"


def test_disposition_round_trips():
    record = make_record().model_copy(update={"disposition": "for_sale"})
    data = json.loads(record.model_dump_json())
    assert data["disposition"] == "for_sale"
    assert CardRecord.model_validate(data).disposition == "for_sale"
```

(Ensure `json` and `CardRecord` are imported at the top of the test file; add if missing.)

**Step 2: Run to verify failure**

Run: `cd server && .venv/bin/pytest tests/test_hive_record.py -q`
Expected: FAIL — `ValidationError`/`AttributeError` on `disposition`.

**Step 3: Implement** — in `server/app/hive/record.py`, add to `CardRecordDraft` right after `asking_price`:

```python
    asking_price: Optional[float] = None
    disposition: Literal["personal", "for_sale"] = "personal"
```

**Step 4: Run to verify pass**

Run: `cd server && .venv/bin/pytest tests/test_hive_record.py -q`
Expected: PASS (all tests in file).

**Step 5: Commit**

```bash
git add server/app/hive/record.py server/tests/test_hive_record.py
git commit -m "feat(server): disposition field on the card record"
```

---

### Task 3: Server — `forsale` Hive tag

**Files:**
- Modify: `server/app/hive/record.py:76-89` (`build_tags`)
- Modify: `server/app/hive/post_builder.py:80-89` (`build_post` call site)
- Test: `server/tests/test_hive_post_builder.py`

**Step 1: Write the failing tests** (append to `server/tests/test_hive_post_builder.py`):

```python
def test_for_sale_card_gets_forsale_tag():
    record = make_record().model_copy(update={"disposition": "for_sale"})
    ops = build_post(record, community=COMMUNITY, account="thebinder",
                     permlink="card-test")
    tags = json.loads(ops[0][1]["json_metadata"])["tags"]
    # After "cardscanner" so the 8-tag cap can never truncate it.
    assert tags.index("forsale") == 3
    assert len(tags) <= 8


def test_personal_card_has_no_forsale_tag():
    ops = build_post(make_record(), community=COMMUNITY, account="thebinder",
                     permlink="card-test")
    assert "forsale" not in json.loads(ops[0][1]["json_metadata"])["tags"]
```

(Reuse the file's existing imports/`make_record`; add `json` import if missing.)

**Step 2: Run to verify failure**

Run: `cd server && .venv/bin/pytest tests/test_hive_post_builder.py -q`
Expected: FAIL — `'forsale' is not in list`.

**Step 3: Implement.** In `record.py`, extend `build_tags` with a keyword flag (community stays first — that assigns the post to the community):

```python
def build_tags(identity: Identity, community: str, *, for_sale: bool = False) -> list[str]:
    """First tag MUST be the community (that's what assigns the post to it).

    Derived tags are sc- prefixed so they always start with a letter and never
    collide with organic tags. Hive tags must match [a-z][a-z0-9-]*.
    """
    tags = [community, GAME_TAG[identity.category], "cardscanner"]
    if for_sale:
        tags.append("forsale")
    for raw, cap in ((identity.subject, 32), (identity.year, 12), (identity.set_name, 24)):
        slug = slugify(raw or "", cap)
        if slug:
            tag = f"sc-{slug}"
            if tag not in tags:
                tags.append(tag)
    return tags[:8]
```

In `post_builder.py` `build_post`, change the tags line:

```python
        "tags": build_tags(record.identity, community,
                           for_sale=record.disposition == "for_sale"),
```

**Step 4: Run to verify pass**

Run: `cd server && .venv/bin/pytest tests/test_hive_post_builder.py tests/test_hive_record.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add server/app/hive/record.py server/app/hive/post_builder.py server/tests/test_hive_post_builder.py
git commit -m "feat(server): forsale Hive tag for for-sale cards"
```

---

### Task 4: Server — fix the update-job no-op bug in PublishQueue

The bug: `tick()` checks "does this post already exist on chain with my record_id?" and confirms without broadcasting if so (`server/app/hive/queue.py:188-191`). Correct for `create` (crash-after-broadcast recovery), fatally wrong for `update` — an edit's post *always* already exists, so every update job (including today's `refresh-comps`) confirms as a silent no-op. Same flaw in the post-exception verify at line 207-210. For updates, re-broadcasting is inherently safe (same permlink = edit, never a duplicate), so updates skip both existence checks and simply retry on failure. Updates also drop the `comment_options` op — options were set at create time, and re-broadcasting them after votes exist can make the whole edit transaction fail.

**Files:**
- Modify: `server/app/hive/queue.py:186-217`
- Test: `server/tests/test_publish_queue.py`

**Step 1: Write the failing tests** (append to `server/tests/test_publish_queue.py`; `FakeHive` there already stores broadcast ops and serves `get_post` — check its exact attribute names and adapt; the pattern below assumes `hive.posts[(account, permlink)]`-style storage that `make_record`/create ticks populate):

```python
async def test_update_broadcasts_even_when_post_exists(queue, hive):
    record = make_record()
    queue.enqueue(record)
    await queue.tick()  # create lands; post now exists on chain
    assert hive.broadcast_calls == 1

    updated = record.model_copy(update={"asking_price": 42.0})
    queue.enqueue(updated, kind="update")
    await queue.tick()
    # The bug: the pre-broadcast existence check confirmed the update
    # without broadcasting. An edit must always broadcast.
    assert hive.broadcast_calls == 2
    assert queue.get_job(record.record_id).status == "confirmed"


async def test_update_broadcasts_comment_only(queue, hive):
    record = make_record()
    queue.enqueue(record)
    await queue.tick()
    queue.enqueue(record.model_copy(update={"disposition": "for_sale"}),
                  kind="update")
    await queue.tick()
    # comment_options can't be re-broadcast after votes; edits send only
    # the comment op.
    assert [name for name, _ in hive.last_ops] == ["comment"]
```

If `FakeHive` lacks a `last_ops` attribute, add one line to its `broadcast_ops` (`self.last_ops = ops`) as part of Step 1 — test-fixture change, not production code.

**Step 2: Run to verify failure**

Run: `cd server && .venv/bin/pytest tests/test_publish_queue.py -q`
Expected: the first new test FAILS with `broadcast_calls == 1` (the no-op bug, proven). If instead it fails because `FakeHive.get_post` doesn't serve the created post back, extend the fake minimally until the test exercises the real code path, and confirm the assertion failure is `2 != 1`.

**Step 3: Implement** — in `queue.py` `tick()`:

```python
        # Idempotency check (creates only): a crash after broadcast, or a
        # duplicate submit, means the post may already be on chain — then this
        # attempt is a no-op. For updates the post ALWAYS exists (an edit), so
        # this check must not run: re-broadcasting an edit is harmless.
        if job.kind == "create":
            existing = await self.client.get_post(self.client.account, job.permlink)
            if self._matches(existing, job.job_id):
                self._confirm(job)
                return 1.0
```

At the `build_post` call:

```python
        ops = build_post(job.record, community=self.community,
                         account=self.client.account, permlink=job.permlink)
        if job.kind == "update":
            # comment_options was set at create time and may be rejected on
            # re-broadcast once the post has votes; edits send comment only.
            ops = [op for op in ops if op[0] == "comment"]
```

In the exception handler, guard the landed-check the same way (an update can't verify "did my edit land" by existence, so it just retries — safe):

```python
        except Exception as exc:  # noqa: BLE001 — any failure gets the verify path
            if job.kind == "create":
                await self.sleeper(BLOCK_SECONDS * 3)
                landed = await self.client.get_post(self.client.account, job.permlink)
                if self._matches(landed, job.job_id):
                    self._confirm(job)  # it DID land; re-broadcasting would duplicate
                    return 1.0
            job.attempts += 1
            ...  # rest unchanged
```

**Step 4: Run the whole server suite** (this touches the queue's core loop):

Run: `cd server && .venv/bin/pytest -q`
Expected: PASS. If `test_updates_use_short_spacing` breaks, re-read it — it should still pass because its update job broadcasts (now unconditionally).

**Step 5: Commit**

```bash
git add server/app/hive/queue.py server/tests/test_publish_queue.py
git commit -m "fix(server): update jobs must broadcast — existence check is create-only"
```

---

### Task 5: Server — listing-update endpoint

**Files:**
- Modify: `server/app/publish_routes.py` (new route after `refresh_card_comps`, ~line 205)
- Test: `server/tests/test_cards_endpoints.py`

**Step 1: Write the failing tests** (append to `server/tests/test_cards_endpoints.py`; the `client`/`hive`/`records` fixtures there already serve `records[0]` as an on-chain post via `FeedHive` — confirm `FakeHive.get_post` returns it, extend the fake if needed as with Task 4):

```python
def test_update_listing_queues_edit(client, hive, records):
    permlink = card_permlink(records[0])
    resp = client.post(f"/api/cards/{permlink}/update",
                       json={"disposition": "for_sale", "asking_price": 25.0})
    assert resp.status_code == 202
    job = app.state.hive.queue.get_job(records[0].record_id)
    assert job is not None and job.kind == "update"
    assert job.record.disposition == "for_sale"
    assert job.record.asking_price == 25.0


def test_update_listing_unknown_permlink_404s(client):
    resp = client.post("/api/cards/card-nope/update",
                       json={"disposition": "for_sale"})
    assert resp.status_code == 404


def test_update_listing_rejects_bad_disposition(client, records):
    resp = client.post(f"/api/cards/{card_permlink(records[0])}/update",
                       json={"disposition": "auction"})
    assert resp.status_code == 422
```

**Step 2: Run to verify failure**

Run: `cd server && .venv/bin/pytest tests/test_cards_endpoints.py -q`
Expected: FAIL — 404/405 on the new route.

**Step 3: Implement** — in `publish_routes.py`, mirroring `refresh_card_comps` exactly (fetch → validate → patch → enqueue):

```python
class ListingUpdate(BaseModel):
    disposition: Literal["personal", "for_sale"]
    asking_price: Optional[float] = None


@router.post("/api/cards/{permlink}/update", status_code=202)
async def update_listing(request: Request, permlink: str, body: ListingUpdate):
    """Edit a published card's disposition/asking price via a queued post edit."""
    state = _state(request)
    post = await state.client.get_post(state.client.account, permlink)
    entry = _parse_card_post(post) if post else None
    if entry is None:
        raise HTTPException(404, "No such card in The Binder.")
    record = CardRecord.model_validate(entry["card"])
    updated = record.model_copy(update={
        "disposition": body.disposition,
        "asking_price": body.asking_price,
    })
    job = state.queue.enqueue(updated, kind="update")
    return _job_payload(job, state.queue)
```

Add to the file's imports: `from typing import Literal` (extend the existing `typing` import) and `from pydantic import BaseModel` (extend the existing `pydantic` import).

Note `asking_price` is set unconditionally: sending `{"disposition": "personal"}` with no price clears the ask — marking a card back to personal removes the listing price, which is the behavior we want.

**Step 4: Run to verify pass**

Run: `cd server && .venv/bin/pytest -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add server/app/publish_routes.py server/tests/test_cards_endpoints.py
git commit -m "feat(server): listing-update endpoint for published cards"
```

---

### Task 6: Web — types, draft mapping, staging

**Files:**
- Modify: `web/src/binderTypes.ts` (CardRecord ~line 31, StagedCard ~line 59)
- Modify: `web/src/binderApi.ts` (`toDraft` ~line 37, new `updateListing` after `refreshComps`)
- Test: `web/src/binderApi.test.ts`

**Step 1: Write the failing tests** (append to `web/src/binderApi.test.ts`, following its existing `toDraft` test style and fetch-mock helpers):

```typescript
it('toDraft carries disposition, defaulting to personal', () => {
  const staged = makeStaged() // the file's existing helper
  expect(toDraft(staged).disposition).toBe('personal')
  expect(toDraft({ ...staged, disposition: 'for_sale' }).disposition).toBe('for_sale')
})

it('updateListing POSTs disposition and asking price', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ job_id: 'r1' }), { status: 202 }),
  )
  await updateListing('card-x', { disposition: 'for_sale', asking_price: 25 })
  const [url, init] = fetchMock.mock.calls[0]
  expect(String(url)).toContain('/api/cards/card-x/update')
  expect(JSON.parse(init!.body as string)).toEqual({
    disposition: 'for_sale',
    asking_price: 25,
  })
})
```

**Step 2: Run to verify failure**

Run: `cd web && npm test -- --run binderApi`
Expected: FAIL — `disposition` missing / `updateListing` not exported.

**Step 3: Implement.**

`binderTypes.ts` — add the shared type and the two fields (keep the server-mirror comment discipline):

```typescript
export type Disposition = 'personal' | 'for_sale'
```

In `CardRecord`, after `asking_price`:

```typescript
  asking_price: number | null
  disposition: Disposition
```

In `StagedCard`, after `askingPrice` (optional — pre-existing staged rows lack it; treat absent as `'personal'` at read sites):

```typescript
  disposition?: Disposition
```

`binderApi.ts` — in `toDraft`, after `asking_price`:

```typescript
    asking_price: staged.askingPrice ?? null,
    disposition: staged.disposition ?? 'personal',
```

New function after `refreshComps`:

```typescript
export function updateListing(
  permlink: string,
  body: { disposition: Disposition; asking_price: number | null },
): Promise<PublishJobStatus> {
  return request(`/api/cards/${encodeURIComponent(permlink)}/update`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}
```

Import `Disposition` in `binderApi.ts`. TypeScript will now flag any `CardRecord` literal in tests missing `disposition` — fix those fixtures with `disposition: 'personal'`.

**Step 4: Run to verify pass**

Run: `cd web && npm test -- --run`
Expected: PASS (whole web suite — the required-field addition can ripple into other test fixtures).

**Step 5: Commit**

```bash
git add web/src/binderTypes.ts web/src/binderApi.ts web/src/binderApi.test.ts
git commit -m "feat(web): disposition in card types, draft mapping, updateListing API"
```

---

### Task 7: Web — disposition toggle on the Results screen

The toggle renders only when the caller wires it (fresh scans / staged history entries). Binder detail reuses `ResultsScreen` for other people's published cards and passes nothing, so it stays read-only there.

**Files:**
- Modify: `web/src/screens/ResultsScreen.tsx` (props + one section before the closing button)
- Modify: `web/src/App.tsx` (track the staged record, wire the toggle)
- Test: `web/src/screens/ResultsScreen.test.tsx`

**Step 1: Write the failing tests** (append to `ResultsScreen.test.tsx`, using its existing result fixture):

```typescript
it('renders the disposition toggle when wired, defaulting to personal', () => {
  const onDisposition = vi.fn()
  render(<ResultsScreen result={okResult} onRescan={() => {}}
                        disposition="personal" onDisposition={onDisposition} />)
  const forSale = screen.getByRole('radio', { name: /for sale/i })
  expect(screen.getByRole('radio', { name: /personal collection/i })).toBeChecked()
  fireEvent.click(forSale)
  expect(onDisposition).toHaveBeenCalledWith('for_sale')
})

it('hides the toggle when not wired (published cards)', () => {
  render(<ResultsScreen result={okResult} onRescan={() => {}} />)
  expect(screen.queryByRole('radio', { name: /for sale/i })).toBeNull()
})
```

**Step 2: Run to verify failure**

Run: `cd web && npm test -- --run ResultsScreen`
Expected: FAIL — no radio rendered.

**Step 3: Implement.** In `ResultsScreen.tsx`:

```typescript
import type { Disposition } from '../binderTypes'

interface Props {
  result: ScanResponse
  onRescan: () => void
  disposition?: Disposition
  onDisposition?: (d: Disposition) => void
}
```

Before the `<button onClick={onRescan}>` line, inside the happy path (photo ok):

```tsx
      {disposition && onDisposition && (
        <fieldset className="disposition">
          <legend>This card is…</legend>
          <label>
            <input type="radio" name="disposition" value="personal"
                   checked={disposition === 'personal'}
                   onChange={() => onDisposition('personal')} />
            Personal collection
          </label>
          <label>
            <input type="radio" name="disposition" value="for_sale"
                   checked={disposition === 'for_sale'}
                   onChange={() => onDisposition('for_sale')} />
            For sale
          </label>
        </fieldset>
      )}
```

In `App.tsx`: `stageScan` already returns the `StagedCard` — stop discarding it. Add state and wiring:

```typescript
  const [lastStaged, setLastStaged] = useState<StagedCard | null>(null)
```

In `handleScan`, replace `await stageScan(...)` with:

```typescript
        const staged = await stageScan(response, askingPrice, prepFront, prepBack)
        setLastStaged(staged)
```

(and `setLastStaged(null)` alongside `setLastResult(response)` before staging, so a failed staging write never leaves a stale toggle). In the results view:

```tsx
          <ResultsScreen
            result={lastResult}
            onRescan={() => setView('scan')}
            disposition={lastStaged ? (lastStaged.disposition ?? 'personal') : undefined}
            onDisposition={async d => {
              if (!lastStaged) return
              await setStatus(lastStaged.record_id, { disposition: d })
              setLastStaged({ ...lastStaged, disposition: d })
            }}
          />
```

The History `onSelect` handler also sets `setLastStaged(entry)` (draft entries get the toggle; published entries edit via Task 8's History flow instead, so only pass staged cards whose `status === 'draft'` — otherwise `setLastStaged(null)`). Import `setStatus` from `./binderDb` and `StagedCard`/`Disposition` types.

**Step 4: Run to verify pass**

Run: `cd web && npm test -- --run`
Expected: PASS.

**Step 5: Commit**

```bash
git add web/src/screens/ResultsScreen.tsx web/src/screens/ResultsScreen.test.tsx web/src/App.tsx
git commit -m "feat(web): personal/for-sale toggle on scan results"
```

---

### Task 8: Web — History badges + editing (draft and published)

**Files:**
- Modify: `web/src/screens/HistoryScreen.tsx`
- Test: `web/src/screens/HistoryScreen.test.tsx`

**Step 1: Write the failing tests** (append, using the file's existing staged-card fixtures and IndexedDB/fetch mocks):

```typescript
it('shows a For sale badge on for-sale cards', async () => {
  await seedStaged({ ...makeStaged(), disposition: 'for_sale' })
  render(<HistoryScreen onSelect={() => {}} />)
  expect(await screen.findByText('For sale')).toBeInTheDocument()
})

it('marks a published card for sale via the update endpoint', async () => {
  await seedStaged({ ...makeStaged(), status: 'published', permlink: 'card-x' })
  const fetchMock = mockFetch({ job_id: 'r1' }) // file's existing helper style
  render(<HistoryScreen onSelect={() => {}} />)
  fireEvent.click(await screen.findByRole('button', { name: /mark for sale/i }))
  fireEvent.change(screen.getByLabelText(/asking price/i), { target: { value: '25' } })
  fireEvent.click(screen.getByRole('button', { name: /^save$/i }))
  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/cards/card-x/update'),
      expect.objectContaining({ method: 'POST' }),
    )
  })
})
```

Adapt helper names to what the test file actually has — the behavior under test is the contract, not the helper spelling.

**Step 2: Run to verify failure**

Run: `cd web && npm test -- --run HistoryScreen`
Expected: FAIL.

**Step 3: Implement.** In `HistoryScreen.tsx`:

- Row badge, next to the existing status chip: `{(card.disposition ?? 'personal') === 'for_sale' && <span className="chip chip-forsale">For sale</span>}`
- Per-row action:
  - **Draft** rows: a plain toggle button — "Mark for sale" / "Mark personal" — that calls `setStatus(card.record_id, { disposition })` and `refresh()`. No network.
  - **Published** rows (`status === 'published' && card.permlink`): "Mark for sale" opens a small inline form (asking-price `<input type="number">` prefilled from `card.askingPrice`, Save/Cancel). Save calls `updateListing(card.permlink, { disposition: 'for_sale', asking_price: price })`, then mirrors locally with `setStatus(card.record_id, { disposition: 'for_sale', askingPrice: price ?? undefined })`, then `refresh()`. Already-for-sale published rows get "Mark personal" → `updateListing(card.permlink, { disposition: 'personal', asking_price: null })` + local mirror (clearing `askingPrice`). Wrap in the screen's existing try/catch-`setError` pattern; on failure show the error and skip the local mirror.
- A caption after a successful published-card update: reuse the refresh-comps wording — "Update queued — the post updates in a few minutes."

Import `updateListing` from `../binderApi`.

**Step 4: Run to verify pass**

Run: `cd web && npm test -- --run`
Expected: PASS.

**Step 5: Commit**

```bash
git add web/src/screens/HistoryScreen.tsx web/src/screens/HistoryScreen.test.tsx
git commit -m "feat(web): history badges and listing editing, local and on-chain"
```

---

### Task 9: Web — Binder badge + For-sale filter

**Files:**
- Modify: `web/src/screens/BinderScreen.tsx`
- Test: `web/src/screens/BinderScreen.test.tsx`

**Step 1: Write the failing tests** (append; the file mocks `listBinder` — give one card `disposition: 'for_sale'`, one `'personal'`):

```typescript
it('filters to for-sale cards', async () => {
  render(<BinderScreen />)
  await screen.findByText(forSaleCard.card.identity.subject)
  fireEvent.click(screen.getByRole('button', { name: /for sale/i }))
  expect(screen.queryByText(personalCard.card.identity.subject)).toBeNull()
  expect(screen.getByText(forSaleCard.card.identity.subject)).toBeInTheDocument()
})
```

**Step 2: Run to verify failure**

Run: `cd web && npm test -- --run BinderScreen`
Expected: FAIL.

**Step 3: Implement.** In `BinderScreen.tsx`:

```typescript
  const [forSaleOnly, setForSaleOnly] = useState(false)
```

Filter chip next to the search input:

```tsx
      <button
        className={forSaleOnly ? 'chip chip-active' : 'chip'}
        aria-pressed={forSaleOnly}
        onClick={() => setForSaleOnly(v => !v)}
      >
        For sale
      </button>
```

Extend the visible-cards line:

```typescript
  const visible = cards
    .filter(c => !forSaleOnly || c.card.disposition === 'for_sale')
    .filter(c => !query || matches(c.card, query))
```

Row badge next to the existing `.ask` span: `{item.card.disposition === 'for_sale' && <span className="chip chip-forsale">For sale</span>}`. Same badge in the detail view near the asking price.

(Filtering is client-side over loaded pages, same as search — consistent with the screen's existing behavior; the `forsale` tag covers server-side discovery on Hive frontends.)

**Step 4: Run to verify pass**

Run: `cd web && npm test -- --run`
Expected: PASS.

**Step 5: Commit**

```bash
git add web/src/screens/BinderScreen.tsx web/src/screens/BinderScreen.test.tsx
git commit -m "feat(web): for-sale badge and filter in The Binder"
```

---

### Task 10: Full verification + docs

**Step 1: Run everything**

Run: `make test`
Expected: both suites PASS.

**Step 2: Manual end-to-end** (with the Task 1 `.env`, plus Hive creds if available — otherwise `HIVE_DRY_RUN=true`):

1. `make dev`, scan a sample card → live eBay verdict appears.
2. Flip the Results toggle to "For sale" → History shows the badge.
3. Publish (dry-run ok) → confirm the queued job; with dry-run, check the server log's broadcast ops contain `"disposition":"for_sale"` and the `forsale` tag.
4. On a published card (real Hive only): "Mark personal" → job queues, post updates within the update interval.

**Step 3: Update README** — in the Hive section, one sentence: cards carry a public personal/for-sale flag; for-sale cards are tagged `forsale` and browsable on any Hive frontend.

**Step 4: Commit**

```bash
git add README.md
git commit -m "docs: personal/for-sale disposition in the Hive section"
```
