import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, expect, test, vi } from 'vitest'
import BinderScreen from './BinderScreen'
import type { BinderCard, CardRecord } from '../binderTypes'

const mocks = vi.hoisted(() => ({ listBinder: vi.fn(), refreshComps: vi.fn() }))

vi.mock('../binderApi', async importOriginal => ({
  ...(await importOriginal<typeof import('../binderApi')>()),
  listBinder: mocks.listBinder,
  refreshComps: mocks.refreshComps,
}))

function card(player: string, year = '2018'): CardRecord {
  return {
    v: 1, kind: 'card', record_id: `rec-${player}`,
    identity: { subject: player, year, set_name: 'Panini Prizm', card_number: '280',
                variant: null, search_string: `${year} ${player}`, confidence: 0.9 },
    condition: { observations: [], grade_low: 6, grade_high: 8 },
    slab: null, authenticity: { red_flags: [], risk: 'low' },
    verdict: { value_low: 60, value_high: 90, verdict: 'undervalued', reasoning: 'cheap' },
    comps: { summary: { source: 'active_listings', raw_count: 12, raw_low: 30,
                        raw_median: 47, graded_count: 0, graded_low: null,
                        graded_median: null }, top_sales: [], as_of: '2026-08-16' },
    images: { front: 'https://images.hive.blog/DQm/front.jpg', back: null },
    asking_price: 55, attribution: { client_id: 'c1', display_name: null },
    scanned_at: '2026-08-16T12:00:00Z',
  }
}

function entry(player: string, permlink: string): BinderCard {
  return { permlink, author: 'thebinder', created: '2026-08-16T20:30:00', card: card(player) }
}

beforeEach(() => {
  mocks.listBinder.mockReset()
  mocks.refreshComps.mockReset()
})

test('renders the community feed', async () => {
  mocks.listBinder.mockResolvedValue({
    cards: [entry('Luka Doncic', 'card-luka'), entry('Jayson Tatum', 'card-tatum')],
    next: null,
  })
  render(<BinderScreen />)
  expect(await screen.findByText(/Luka Doncic/)).toBeTruthy()
  expect(screen.getByText(/Jayson Tatum/)).toBeTruthy()
})

test('search filters client-side', async () => {
  mocks.listBinder.mockResolvedValue({
    cards: [entry('Luka Doncic', 'card-luka'), entry('Jayson Tatum', 'card-tatum')],
    next: null,
  })
  render(<BinderScreen />)
  await screen.findByText(/Luka Doncic/)
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'tatum' } })
  expect(screen.queryByText(/Luka Doncic/)).toBeNull()
  expect(screen.getByText(/Jayson Tatum/)).toBeTruthy()
})

test('load more follows the cursor', async () => {
  mocks.listBinder
    .mockResolvedValueOnce({ cards: [entry('Luka Doncic', 'card-luka')],
                             next: { start_author: 'thebinder', start_permlink: 'card-luka' } })
    .mockResolvedValueOnce({ cards: [entry('Jayson Tatum', 'card-tatum')], next: null })
  render(<BinderScreen />)
  await screen.findByText(/Luka Doncic/)
  fireEvent.click(screen.getByRole('button', { name: /load more/i }))
  expect(await screen.findByText(/Jayson Tatum/)).toBeTruthy()
  expect(mocks.listBinder).toHaveBeenLastCalledWith(
    { start_author: 'thebinder', start_permlink: 'card-luka' })
  expect(screen.queryByRole('button', { name: /load more/i })).toBeNull() // end of feed
})

test('card detail shows image, hive link, and comps refresh', async () => {
  mocks.listBinder.mockResolvedValue({ cards: [entry('Luka Doncic', 'card-luka')], next: null })
  mocks.refreshComps.mockResolvedValue({ job_id: 'rec-Luka Doncic' })
  render(<BinderScreen />)
  fireEvent.click(await screen.findByText(/Luka Doncic/))
  const link = await screen.findByRole('link', { name: /view on hive/i })
  expect(link.getAttribute('href')).toBe('https://peakd.com/@thebinder/card-luka')
  expect((screen.getByAltText(/card front/i) as HTMLImageElement).src)
    .toBe('https://images.hive.blog/DQm/front.jpg')
  fireEvent.click(screen.getByRole('button', { name: /refresh comps/i }))
  expect(await screen.findByText(/refresh queued/i)).toBeTruthy()
  expect(mocks.refreshComps).toHaveBeenCalledWith('card-luka')
})

test('feed failure shows a readable error', async () => {
  mocks.listBinder.mockRejectedValue(new Error('down'))
  render(<BinderScreen />)
  expect((await screen.findByRole('alert')).textContent).toMatch(/could not load/i)
})
