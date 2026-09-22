import 'fake-indexeddb/auto'
import { beforeEach, expect, test } from 'vitest'
import {
  _resetDbForTests,
  clientId,
  deleteStaged,
  getImages,
  listStaged,
  migrateFromLocalStorage,
  pruneStaged,
  setStatus,
  stageScan,
} from './binderDb'
import { pushHistory } from './storage'
import type { ScanResponse } from './types'

const RESPONSE = { vision: { photo_ok: true } } as unknown as ScanResponse

beforeEach(async () => {
  localStorage.clear()
  await _resetDbForTests()
})

function blob(text: string) {
  return new Blob([text], { type: 'image/jpeg' })
}

test('stageScan stores a draft with images, newest first', async () => {
  const first = await stageScan(RESPONSE, 12, blob('front-1'), null)
  const second = await stageScan(RESPONSE, null, blob('front-2'), blob('back-2'))
  expect(first.status).toBe('draft')
  expect(first.record_id).not.toBe(second.record_id)
  const staged = await listStaged()
  expect(staged.map(s => s.record_id)).toEqual([second.record_id, first.record_id])
  expect(staged[1].askingPrice).toBe(12)
})

test('images round-trip', async () => {
  const card = await stageScan(RESPONSE, null, blob('front'), blob('back'))
  const images = await getImages(card.record_id)
  expect(await images?.front.text()).toBe('front')
  expect(await images?.back?.text()).toBe('back')
  const only = await stageScan(RESPONSE, null, blob('solo'), null)
  expect((await getImages(only.record_id))?.back).toBeNull()
})

test('setStatus patches and deleteStaged removes card plus images', async () => {
  const card = await stageScan(RESPONSE, null, blob('x'), null)
  await setStatus(card.record_id, { status: 'queued', job_id: 'j1' })
  expect((await listStaged())[0]).toMatchObject({ status: 'queued', job_id: 'j1' })
  await deleteStaged(card.record_id)
  expect(await listStaged()).toEqual([])
  expect(await getImages(card.record_id)).toBeNull()
})

test('migrateFromLocalStorage imports history once as unpublishable legacy drafts', async () => {
  pushHistory({ at: '2026-08-01T00:00:00Z', response: RESPONSE, askingPrice: 5 })
  pushHistory({ at: '2026-08-02T00:00:00Z', response: RESPONSE })
  expect(await migrateFromLocalStorage()).toBe(2)
  const staged = await listStaged()
  expect(staged).toHaveLength(2)
  expect(staged.every(s => s.legacy && s.status === 'draft')).toBe(true)
  expect(await migrateFromLocalStorage()).toBe(0) // idempotent
  expect(await listStaged()).toHaveLength(2)
})

test('prune drops oldest drafts only, never published cards', async () => {
  const oldest = await stageScan(RESPONSE, null, blob('a'), null)
  const published = await stageScan(RESPONSE, null, blob('b'), null)
  await stageScan(RESPONSE, null, blob('c'), null)
  await stageScan(RESPONSE, null, blob('d'), null)
  await setStatus(published.record_id, { status: 'published' })
  await pruneStaged(2)
  const kept = (await listStaged()).map(s => s.record_id)
  expect(kept).toContain(published.record_id) // published survives over-limit
  expect(kept).not.toContain(oldest.record_id)
  expect(await getImages(oldest.record_id)).toBeNull() // images pruned too
})

test('listStaged migrates legacy player identities to subject in memory', async () => {
  // Entries staged before the Identity.player -> subject rename.
  const legacy = {
    vision: {
      photo_ok: true, photo_issue: null,
      identity: { player: 'Luka Doncic', year: '2018', set_name: 'Panini Prizm',
                  card_number: '280', variant: null,
                  search_string: '2018 Panini Prizm Luka Doncic #280', confidence: 0.92 },
      condition: null, authenticity: null, ai_value_note: null,
    },
    comps: null, comps_error: null, verdict: null,
  } as unknown as ScanResponse
  await stageScan(legacy, null, blob('f'), null)
  const identity = (await listStaged())[0].response.vision.identity!
  expect(identity.subject).toBe('Luka Doncic')
  expect('player' in identity).toBe(false)
  expect(identity.year).toBe('2018') // rest of the identity is untouched
})

test('clientId is stable across calls', () => {
  const id = clientId()
  expect(id).toMatch(/[0-9a-f-]{36}/)
  expect(clientId()).toBe(id)
})
