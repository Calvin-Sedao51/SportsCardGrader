import 'fake-indexeddb/auto'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, test, vi } from 'vitest'
import HistoryScreen from './HistoryScreen'
import { _resetDbForTests, listStaged, setStatus, stageScan } from '../binderDb'
import type { ScanResponse } from '../types'

const mocks = vi.hoisted(() => ({
  publishCard: vi.fn(),
  getPublishStatus: vi.fn(),
  resumePendingPublishes: vi.fn(),
}))

vi.mock('../binderApi', async importOriginal => ({
  ...(await importOriginal<typeof import('../binderApi')>()),
  publishCard: mocks.publishCard,
  getPublishStatus: mocks.getPublishStatus,
  resumePendingPublishes: mocks.resumePendingPublishes,
}))

beforeEach(async () => {
  localStorage.clear()
  await _resetDbForTests()
  mocks.publishCard.mockReset()
  mocks.getPublishStatus.mockReset()
  mocks.resumePendingPublishes.mockReset().mockResolvedValue(undefined)
})

const response: ScanResponse = {
  vision: {
    photo_ok: true, photo_issue: null,
    identity: { subject: 'Luka Doncic', year: '2018', set_name: 'Panini Prizm',
                card_number: '280', variant: null,
                search_string: '2018 Panini Prizm Luka Doncic #280', confidence: 0.92 },
    condition: { observations: [], grade_low: 6, grade_high: 8 },
    authenticity: { red_flags: [], risk: 'low' }, ai_value_note: null,
  },
  comps: null,
  comps_error: null,
  verdict: { value_low: 60, value_high: 90, verdict: 'undervalued', reasoning: 'Cheap!' },
}

function blob(text = 'img') {
  return new Blob([text], { type: 'image/jpeg' })
}

test('empty state shows "No scans yet."', async () => {
  render(<HistoryScreen onSelect={() => {}} />)
  expect(await screen.findByText(/no scans yet/i)).toBeTruthy()
})

test('renders staged rows and onSelect fires with the card', async () => {
  await stageScan(response, 50, blob(), null)
  const onSelect = vi.fn()
  render(<HistoryScreen onSelect={onSelect} />)
  const row = await screen.findByText(/Luka Doncic/)
  expect(screen.getByText('Undervalued')).toBeTruthy() // human label, not raw enum
  expect(screen.getByText('Draft')).toBeTruthy()
  fireEvent.click(row)
  expect(onSelect).toHaveBeenCalledOnce()
  expect(onSelect.mock.calls[0][0].response).toEqual(response)
  expect(onSelect.mock.calls[0][0].askingPrice).toBe(50)
})

test('publish flow: consent dialog, then queued chip', async () => {
  const card = await stageScan(response, null, blob(), null)
  mocks.publishCard.mockResolvedValue({
    job_id: card.record_id, permlink: 'card-luka', status: 'queued',
    position: 2, eta_seconds: 300, hive_url: null, last_error: null,
  })
  render(<HistoryScreen onSelect={() => {}} />)
  fireEvent.click(await screen.findByRole('button', { name: /publish to the binder/i }))
  const dialog = await screen.findByRole('dialog')
  expect(dialog.textContent).toMatch(/public/i)
  expect(dialog.textContent).toMatch(/permanent/i)
  expect(mocks.publishCard).not.toHaveBeenCalled() // nothing sent before consent
  fireEvent.click(screen.getByRole('button', { name: /^publish$/i }))
  await screen.findByText(/in queue/i)
  expect(mocks.publishCard).toHaveBeenCalledOnce()
  expect((await listStaged())[0]).toMatchObject({ status: 'queued', job_id: card.record_id })
})

test('consent can be declined', async () => {
  await stageScan(response, null, blob(), null)
  render(<HistoryScreen onSelect={() => {}} />)
  fireEvent.click(await screen.findByRole('button', { name: /publish to the binder/i }))
  fireEvent.click(screen.getByRole('button', { name: /cancel/i }))
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(mocks.publishCard).not.toHaveBeenCalled()
})

test('legacy rows cannot be published', async () => {
  const card = await stageScan(response, null, blob(), null)
  await setStatus(card.record_id, { legacy: true })
  render(<HistoryScreen onSelect={() => {}} />)
  await screen.findByText(/Luka Doncic/)
  expect(screen.queryByRole('button', { name: /publish to the binder/i })).toBeNull()
})

test('published rows link to the hive post', async () => {
  const card = await stageScan(response, null, blob(), null)
  await setStatus(card.record_id, {
    status: 'published', hive_url: 'https://peakd.com/@thebinder/card-luka',
  })
  render(<HistoryScreen onSelect={() => {}} />)
  const link = await screen.findByRole('link', { name: /view on hive/i })
  expect(link.getAttribute('href')).toBe('https://peakd.com/@thebinder/card-luka')
})

test('poller promotes queued cards when the server confirms', async () => {
  const card = await stageScan(response, null, blob(), null)
  await setStatus(card.record_id, { status: 'queued', job_id: card.record_id })
  mocks.getPublishStatus.mockResolvedValue({
    job_id: card.record_id, permlink: 'card-luka', status: 'confirmed',
    position: 0, eta_seconds: 0,
    hive_url: 'https://peakd.com/@thebinder/card-luka', last_error: null,
  })
  render(<HistoryScreen onSelect={() => {}} />)
  await screen.findByText('Published')
  await waitFor(async () =>
    expect((await listStaged())[0].status).toBe('published'))
})
