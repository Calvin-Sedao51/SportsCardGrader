import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import ResultsScreen from './ResultsScreen'
import type { ScanResponse } from '../types'

const base: ScanResponse = {
  vision: {
    photo_ok: true, photo_issue: null,
    identity: { subject: 'Luka Doncic', category: 'sports', year: '2018',
                set_name: 'Panini Prizm', card_number: '280', variant: null,
                search_string: '2018 Panini Prizm Luka Doncic #280', confidence: 0.92 },
    condition: { observations: [], grade_low: 6, grade_high: 8 },
    authenticity: { red_flags: [], risk: 'low' }, ai_value_note: null,
  },
  comps: { source: 'active_listings', raw_count: 4, raw_low: 60, raw_median: 90,
           graded_count: 0, graded_low: null, graded_median: null },
  comps_error: null,
  verdict: { value_low: 60, value_high: 90, verdict: 'undervalued', reasoning: 'Cheap!' },
}

test('shows identity, grade range, and verdict', () => {
  render(<ResultsScreen result={base} onRescan={() => {}} />)
  expect(screen.getByText(/Luka Doncic/)).toBeTruthy()
  expect(screen.getByText(/PSA 6–8/)).toBeTruthy()
  expect(screen.getByText(/undervalued/i)).toBeTruthy()
})

test('bad photo shows retake prompt', () => {
  const r: ScanResponse = { vision: { photo_ok: false, photo_issue: 'too much glare',
    identity: null, condition: null, authenticity: null, ai_value_note: null },
    comps: null, comps_error: null, verdict: null }
  render(<ResultsScreen result={r} onRescan={() => {}} />)
  expect(screen.getByText(/glare/)).toBeTruthy()
  expect(screen.getByRole('button', { name: /retake/i })).toBeTruthy()
})

test('comps failure still shows grade with value unavailable', () => {
  const r = { ...base, comps: null, verdict: null, comps_error: 'eBay down' }
  render(<ResultsScreen result={r} onRescan={() => {}} />)
  expect(screen.getByText(/PSA 6–8/)).toBeTruthy()
  expect(screen.getByText(/value unavailable/i)).toBeTruthy()
})

test('authenticity red flags surface prominently', () => {
  const r = structuredClone(base)
  r.vision.authenticity = { red_flags: ['print pattern looks off'], risk: 'high' }
  render(<ResultsScreen result={r} onRescan={() => {}} />)
  expect(screen.getByText(/print pattern looks off/)).toBeTruthy()
})

test('half grades render without trailing zeros', () => {
  const r = structuredClone(base)
  r.vision.condition = { observations: [], grade_low: 6.5, grade_high: 8.5 }
  render(<ResultsScreen result={r} onRescan={() => {}} />)
  expect(screen.getByText(/PSA 6\.5–8\.5/)).toBeTruthy()
})

test('low-confidence identity shows uncertainty warning', () => {
  const r = structuredClone(base)
  r.vision.identity!.confidence = 0.4
  render(<ResultsScreen result={r} onRescan={() => {}} />)
  expect(screen.getByText(/identification uncertain — price may not be reliable/i)).toBeTruthy()
})

test('sold-comps link targets an eBay sold search with the encoded search string', () => {
  render(<ResultsScreen result={base} onRescan={() => {}} />)
  const link = screen.getByRole('link', { name: /see sold comps on ebay/i })
  expect(link.getAttribute('href')).toBe(
    'https://www.ebay.com/sch/i.html?_nkw=2018%20Panini%20Prizm%20Luka%20Doncic%20%23280&_sacat=212&LH_Sold=1&LH_Complete=1',
  )
  expect(link.getAttribute('href')).toContain('LH_Sold=1')
  expect(link.getAttribute('target')).toBe('_blank')
  expect(link.getAttribute('rel')).toBe('noopener noreferrer')
  // Asks-vs-solds distinction is explained when the estimate comes from asks.
  expect(screen.getByText(/what buyers actually paid/i)).toBeTruthy()
})

test('links sold comps to the CCG category for TCG cards', () => {
  const r = structuredClone(base)
  r.vision.identity!.category = 'pokemon'
  r.vision.identity!.subject = 'Charizard'
  r.vision.identity!.search_string = '1999 Pokemon Base Set Charizard #4 Holo 1st Edition'
  render(<ResultsScreen result={r} onRescan={() => {}} />)
  const link = screen.getByRole('link', { name: /sold comps/i })
  expect(link.getAttribute('href')).toContain('_sacat=183454')
})

test('sold-comps link has no category filter for "other" cards', () => {
  const r = structuredClone(base)
  r.vision.identity!.category = 'other'
  render(<ResultsScreen result={r} onRescan={() => {}} />)
  const link = screen.getByRole('link', { name: /sold comps/i })
  expect(link.getAttribute('href')).not.toContain('_sacat')
})

test('sold-comps link defaults to the sports category when category is absent', () => {
  // Staged scans from before Identity.category existed.
  const r = structuredClone(base)
  delete r.vision.identity!.category
  render(<ResultsScreen result={r} onRescan={() => {}} />)
  const link = screen.getByRole('link', { name: /sold comps/i })
  expect(link.getAttribute('href')).toContain('_sacat=212')
})

test('sold-comps link still offered when comps failed', () => {
  const r = { ...base, comps: null, verdict: null, comps_error: 'eBay down' }
  render(<ResultsScreen result={r} onRescan={() => {}} />)
  expect(screen.getByRole('link', { name: /see sold comps on ebay/i })).toBeTruthy()
  // No estimate above to contrast against, so no asks-vs-solds caption.
  expect(screen.queryByText(/what buyers actually paid/i)).toBeNull()
})

test('no sold-comps link without an identity (bad photo)', () => {
  const r: ScanResponse = { vision: { photo_ok: false, photo_issue: 'too much glare',
    identity: null, condition: null, authenticity: null, ai_value_note: null },
    comps: null, comps_error: null, verdict: null }
  render(<ResultsScreen result={r} onRescan={() => {}} />)
  expect(screen.queryByRole('link', { name: /see sold comps on ebay/i })).toBeNull()
})

test('asks-vs-solds caption omitted when comps already come from solds', () => {
  const r = structuredClone(base)
  r.comps!.source = 'sold'
  render(<ResultsScreen result={r} onRescan={() => {}} />)
  expect(screen.getByRole('link', { name: /see sold comps on ebay/i })).toBeTruthy()
  expect(screen.queryByText(/what buyers actually paid/i)).toBeNull()
})

test('verdict badge shows human label, not raw enum value', () => {
  const r = structuredClone(base)
  r.verdict = { value_low: 60, value_high: 90, verdict: 'no_ask', reasoning: 'No asking price given.' }
  render(<ResultsScreen result={r} onRescan={() => {}} />)
  expect(screen.getByText('Value estimate')).toBeTruthy()
  expect(screen.queryByText('no_ask')).toBeNull()
})

test('authenticity_risk verdict renders caution badge alongside the risk banner', () => {
  const r = structuredClone(base)
  r.vision.authenticity = { red_flags: ['print pattern looks off'], risk: 'high' }
  r.verdict = { value_low: 60, value_high: 90, verdict: 'authenticity_risk',
                reasoning: 'Counterfeit red flags detected.' }
  render(<ResultsScreen result={r} onRescan={() => {}} />)
  const badge = screen.getByText('Caution: authenticity')
  expect(badge.className).toContain('verdict-authenticity_risk')
  // Existing red risk banner still shows its detail — badge and banner agree.
  expect(screen.getByText(/print pattern looks off/)).toBeTruthy()
})

test('low_value verdict renders "too cheap to call" badge', () => {
  const r = structuredClone(base)
  r.verdict = { value_low: 5, value_high: 8, verdict: 'low_value',
                reasoning: 'Commodity-priced card.' }
  render(<ResultsScreen result={r} onRescan={() => {}} />)
  const badge = screen.getByText('Too cheap to call')
  expect(badge.className).toContain('verdict-low_value')
})

test('slab response renders badge and no estimated grade range', () => {
  const r = structuredClone(base)
  r.vision.condition = null // the slab already graded it
  r.vision.slab = { company: 'PSA', grade: '9' }
  r.verdict = { value_low: 100, value_high: 150, verdict: 'fair',
                reasoning: 'PSA 9 copies range $100–$150 based on 4 listings.' }
  render(<ResultsScreen result={r} onRescan={() => {}} />)
  const badge = screen.getByText('PSA 9 slab')
  expect(badge.className).toContain('slab-badge')
  // No "PSA X–Y" estimated-condition heading for a professionally graded card.
  expect(screen.queryByText(/PSA \d+(\.\d+)?–\d+/)).toBeNull()
})

test('slab hides the estimated grade range even if condition sneaks through', () => {
  // Defensive: if the model returns both slab and condition, the slab wins.
  const r = structuredClone(base)
  r.vision.slab = { company: 'BGS', grade: '9.5' }
  render(<ResultsScreen result={r} onRescan={() => {}} />)
  expect(screen.getByText('BGS 9.5 slab')).toBeTruthy()
  expect(screen.queryByText(/PSA 6–8/)).toBeNull()
})

test('non-slab response shows no slab badge', () => {
  render(<ResultsScreen result={base} onRescan={() => {}} />)
  expect(screen.queryByText(/slab/i)).toBeNull()
})

test('high_value verdict renders "verify first" badge', () => {
  const r = structuredClone(base)
  r.verdict = { value_low: 800, value_high: 1500, verdict: 'high_value',
                reasoning: 'High-value card — estimate only.' }
  render(<ResultsScreen result={r} onRescan={() => {}} />)
  const badge = screen.getByText('High value — verify first')
  expect(badge.className).toContain('verdict-high_value')
})
