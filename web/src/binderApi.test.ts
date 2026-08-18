import 'fake-indexeddb/auto'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ApiError } from './api'
import {
  getPublishStatus,
  hiveStatus,
  listBinder,
  publishCard,
  refreshComps,
  resumePendingPublishes,
} from './binderApi'
import { _resetDbForTests, listStaged, setStatus, stageScan } from './binderDb'
import type { StagedCard } from './binderTypes'
import type { ScanResponse } from './types'

const RESPONSE: ScanResponse = {
  vision: {
    photo_ok: true, photo_issue: null,
    identity: { subject: 'Luka Doncic', year: '2018', set_name: 'Panini Prizm',
                card_number: '280', variant: null,
                search_string: '2018 Panini Prizm Luka Doncic #280', confidence: 0.92 },
    condition: { observations: [], grade_low: 6, grade_high: 8 },
    authenticity: { red_flags: [], risk: 'low' }, ai_value_note: null,
  },
  comps: { source: 'active_listings', raw_count: 12, raw_low: 30, raw_median: 47,
           graded_count: 5, graded_low: 90, graded_median: 140 },
  comps_error: null,
  verdict: { value_low: 60, value_high: 90, verdict: 'undervalued', reasoning: 'Cheap!' },
}

const STAGED: StagedCard = {
  record_id: 'rec-1', at: '2026-08-16T12:00:00Z', response: RESPONSE,
  askingPrice: 55, status: 'draft',
}

const JOB = { job_id: 'rec-1', permlink: 'card-luka', status: 'queued', position: 1,
              eta_seconds: 0, hive_url: null, last_error: null }

beforeEach(async () => {
  localStorage.clear()
  await _resetDbForTests()
})
afterEach(() => vi.restoreAllMocks())

function mockFetch(body: unknown, status = 202) {
  // Fresh Response per call: a Body can only be read once.
  return vi.spyOn(globalThis, 'fetch').mockImplementation(() =>
    Promise.resolve(new Response(JSON.stringify(body), { status })))
}

test('publishCard posts multipart record + images and returns the job', async () => {
  const mock = mockFetch(JOB)
  const job = await publishCard(STAGED, new Blob(['f']), new Blob(['b']))
  expect(job.job_id).toBe('rec-1')
  const [url, init] = mock.mock.calls[0]
  expect(String(url)).toContain('/api/publish')
  const form = init!.body as FormData
  const record = JSON.parse(form.get('record') as string)
  expect(record).toMatchObject({
    v: 1, kind: 'card', record_id: 'rec-1', asking_price: 55,
    scanned_at: '2026-08-16T12:00:00Z',
    identity: { subject: 'Luka Doncic' },
    comps: { summary: { raw_median: 47 }, top_sales: [] },
  })
  expect(record.attribution.client_id).toMatch(/[0-9a-f-]{36}/)
  expect(form.get('front')).toBeInstanceOf(Blob)
  expect(form.get('back')).toBeInstanceOf(Blob)
})

test('publishCard refuses a scan with no identity', async () => {
  const unreadable = {
    ...STAGED,
    response: { ...RESPONSE, vision: { ...RESPONSE.vision, identity: null } },
  }
  await expect(publishCard(unreadable, new Blob(['f']), null)).rejects.toThrow(ApiError)
})

test('publishCard surfaces the server detail message', async () => {
  mockFetch({ detail: 'Low Resource Credits' }, 503)
  await expect(publishCard(STAGED, new Blob(['f']), null)).rejects.toThrow(/Resource Credits/)
})

test('read helpers hit the expected endpoints', async () => {
  const mock = mockFetch({ cards: [], next: null }, 200)
  await listBinder({ start_author: 'a', start_permlink: 'p' })
  expect(String(mock.mock.calls[0][0]))
    .toContain('/api/cards?limit=20&start_author=a&start_permlink=p')
  await getPublishStatus('j1')
  expect(String(mock.mock.calls[1][0])).toContain('/api/publish/j1')
  await refreshComps('card-x')
  expect(String(mock.mock.calls[2][0])).toContain('/api/cards/card-x/refresh-comps')
  expect(mock.mock.calls[2][1]?.method).toBe('POST')
})

test('hiveStatus reports unconfigured on network failure', async () => {
  vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('offline'))
  expect(await hiveStatus()).toEqual({ configured: false })
})

test('resumePendingPublishes re-submits consented drafts', async () => {
  const card = await stageScan(RESPONSE, 55, new Blob(['front']), null)
  await setStatus(card.record_id, { publishRequested: true })
  mockFetch({ ...JOB, job_id: card.record_id })
  await resumePendingPublishes()
  const [after] = await listStaged()
  expect(after.status).toBe('queued')
  expect(after.job_id).toBe(card.record_id)
})

test('resumePendingPublishes leaves drafts pending when still offline', async () => {
  const card = await stageScan(RESPONSE, 55, new Blob(['front']), null)
  await setStatus(card.record_id, { publishRequested: true })
  vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('offline'))
  await resumePendingPublishes()
  const [after] = await listStaged()
  expect(after.status).toBe('draft')
  expect(after.publishRequested).toBe(true)
})
