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

function renderResults(result: ScanResponse, askingPrice: number | null = null) {
  return render(<ResultsScreen result={result} askingPrice={askingPrice} onRescan={() => {}} />)
}

test('shows identity, grade range, and verdict', () => {
  renderResults(base)
  expect(screen.getByText(/Luka Doncic/)).toBeTruthy()
  expect(screen.getByText(/PSA 6–8/)).toBeTruthy()
  expect(screen.getByText(/undervalued/i)).toBeTruthy()
})

test('bad photo shows retake prompt', () => {
  const r: ScanResponse = { vision: { photo_ok: false, photo_issue: 'too much glare',
    identity: null, condition: null, authenticity: null, ai_value_note: null },
    comps: null, comps_error: null, verdict: null }
  renderResults(r)
  expect(screen.getByText(/glare/)).toBeTruthy()
  expect(screen.getByRole('button', { name: /retake/i })).toBeTruthy()
})

// --- Verdict hero: delta line ---

test('undervalued with asking price leads with the below-market delta', () => {
  const r = structuredClone(base)
  r.verdict = { value_low: 50, value_high: 70, verdict: 'undervalued', reasoning: 'Cheap!' }
  const { container } = renderResults(r, 20) // midpoint 60 − 20 = 40
  expect(screen.getByText('≈ $40 below market')).toBeTruthy()
  const hero = container.querySelector('.hero')!
  expect(hero.className).toContain('verdict-hero-undervalued')
})

test('overpriced with asking price shows the above-market delta as an absolute value', () => {
  const r = structuredClone(base)
  r.verdict = { value_low: 60, value_high: 90, verdict: 'overpriced', reasoning: 'Steep.' }
  const { container } = renderResults(r, 150) // midpoint 75 − 150 = −75
  expect(screen.getByText('≈ $75 above market')).toBeTruthy()
  expect(container.querySelector('.hero')!.className).toContain('verdict-hero-overpriced')
})

test('fair verdict shows "in range" instead of a delta', () => {
  const r = structuredClone(base)
  r.verdict = { value_low: 60, value_high: 90, verdict: 'fair', reasoning: 'Right in range.' }
  renderResults(r, 70)
  // Ask appears once, in the supporting line — the delta line stays terse.
  expect(screen.getByText('In range')).toBeTruthy()
  expect(screen.getByText(/asking \$70/)).toBeTruthy()
  expect(screen.queryByText(/below market/)).toBeNull()
  expect(screen.queryByText(/above market/)).toBeNull()
})

test('no asking price: no delta line, supporting estimate line only', () => {
  renderResults(base) // askingPrice null
  expect(screen.queryByText(/below market/)).toBeNull()
  expect(screen.queryByText(/above market/)).toBeNull()
  expect(screen.getByText('Est. $60–$90')).toBeTruthy()
})

test('asking price appears on the supporting estimate line', () => {
  const r = structuredClone(base)
  r.verdict = { value_low: 60, value_high: 90, verdict: 'fair', reasoning: 'Right in range.' }
  renderResults(r, 70)
  expect(screen.getByText('Est. $60–$90 · asking $70')).toBeTruthy()
})

// --- Verdict hero: evidence caption ---

test('evidence caption counts current listings', () => {
  renderResults(base)
  expect(screen.getByText('Based on 4 current listings')).toBeTruthy()
})

test('evidence caption adds graded count on the raw path', () => {
  const r = structuredClone(base)
  r.comps!.graded_count = 2
  renderResults(r)
  expect(screen.getByText('Based on 4 current listings, 2 graded')).toBeTruthy()
})

test('slab result (raw_count 0) counts graded listings', () => {
  const r = structuredClone(base)
  r.vision.condition = null
  r.vision.slab = { company: 'PSA', grade: '9' }
  r.comps = { source: 'active_listings', raw_count: 0, raw_low: null, raw_median: null,
              graded_count: 3, graded_low: 100, graded_median: 125 }
  renderResults(r)
  expect(screen.getByText('Based on 3 graded listings')).toBeTruthy()
})

test('sold comps get a sold-listings caption', () => {
  const r = structuredClone(base)
  r.comps!.source = 'sold'
  renderResults(r)
  expect(screen.getByText('Based on 4 sold listings')).toBeTruthy()
})

// --- Comps failure: neutral hero ---

test('comps failure still shows grade with value unavailable', () => {
  const r = { ...base, comps: null, verdict: null, comps_error: 'eBay down' }
  const { container } = renderResults(r)
  expect(screen.getByText(/PSA 6–8/)).toBeTruthy()
  expect(screen.getByText(/value unavailable/i)).toBeTruthy()
  expect(screen.getByText('eBay down')).toBeTruthy()
  expect(container.querySelector('.hero')!.className).toContain('verdict-hero-neutral')
  expect(screen.getByRole('link', { name: /see sold comps on ebay/i })).toBeTruthy()
})

test('neutral hero surfaces the AI rough-value fallback', () => {
  const r = structuredClone(base)
  r.comps = null
  r.verdict = null
  r.comps_error = 'no comps found'
  r.vision.ai_value_note = 'roughly $50-$80 raw'
  renderResults(r)
  expect(screen.getByText(/AI rough estimate \(low confidence\): roughly \$50-\$80 raw/)).toBeTruthy()
})

// --- Authenticity placement ---

test('low risk renders a quiet check-line inside the hero, not a banner', () => {
  const { container } = renderResults(base)
  const check = screen.getByText(/no counterfeit red flags/i)
  expect(container.querySelector('.hero')!.contains(check)).toBe(true)
  expect(container.querySelector('.risk-low')).toBeNull()
})

test('high risk renders a red banner above the hero', () => {
  const r = structuredClone(base)
  r.vision.authenticity = { red_flags: ['print pattern looks off'], risk: 'high' }
  const { container } = renderResults(r)
  expect(screen.getByText(/print pattern looks off/)).toBeTruthy()
  const banner = container.querySelector('.authenticity')!
  expect(banner.className).toContain('risk-high')
  const hero = container.querySelector('.hero')!
  // Banner precedes the hero in document order — risk keeps veto placement.
  expect(banner.compareDocumentPosition(hero) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  // No check-line when risk is not low.
  expect(screen.queryByText(/no counterfeit red flags/i)).toBeNull()
})

test('caution risk renders the amber banner', () => {
  const r = structuredClone(base)
  r.vision.authenticity = { red_flags: ['border looks blurry'], risk: 'caution' }
  const { container } = renderResults(r)
  expect(container.querySelector('.risk-caution')).toBeTruthy()
  expect(screen.getByText(/border looks blurry/)).toBeTruthy()
})

// --- Grade / observations ---

test('half grades render without trailing zeros', () => {
  const r = structuredClone(base)
  r.vision.condition = { observations: [], grade_low: 6.5, grade_high: 8.5 }
  renderResults(r)
  expect(screen.getByText(/PSA 6\.5–8\.5/)).toBeTruthy()
})

test('grade chip is marked as an estimate', () => {
  renderResults(base)
  expect(screen.getByText('PSA 6–8 (est.)')).toBeTruthy()
})

test('observations sit behind a "What the AI saw" disclosure', () => {
  const r = structuredClone(base)
  r.vision.condition = {
    observations: [{ area: 'corners', severity: 'minor', note: 'slight whitening' }],
    grade_low: 6, grade_high: 8,
  }
  const { container } = renderResults(r)
  const details = container.querySelector('details.observations')
  expect(details).toBeTruthy()
  expect(screen.getByText('What the AI saw')).toBeTruthy()
  expect(screen.getByText(/slight whitening/)).toBeTruthy()
})

test('low-confidence identity shows uncertainty warning', () => {
  const r = structuredClone(base)
  r.vision.identity!.confidence = 0.4
  renderResults(r)
  expect(screen.getByText(/identification uncertain — price may not be reliable/i)).toBeTruthy()
})

// --- Sold-comps link ---

test('sold-comps link targets an eBay sold search with the encoded search string', () => {
  const { container } = renderResults(base)
  const link = screen.getByRole('link', { name: /see sold comps on ebay/i })
  expect(link.getAttribute('href')).toBe(
    'https://www.ebay.com/sch/i.html?_nkw=2018%20Panini%20Prizm%20Luka%20Doncic%20%23280&_sacat=212&LH_Sold=1&LH_Complete=1',
  )
  expect(link.getAttribute('href')).toContain('LH_Sold=1')
  expect(link.getAttribute('target')).toBe('_blank')
  expect(link.getAttribute('rel')).toBe('noopener noreferrer')
  // Asks-vs-solds distinction is explained when the estimate comes from asks.
  expect(screen.getByText(/what buyers actually paid/i)).toBeTruthy()
  // Link and caption live inside the hero now.
  expect(container.querySelector('.hero')!.contains(link)).toBe(true)
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
  renderResults(r)
  expect(screen.getByRole('link', { name: /see sold comps on ebay/i })).toBeTruthy()
  // No estimate above to contrast against, so no asks-vs-solds caption.
  expect(screen.queryByText(/what buyers actually paid/i)).toBeNull()
})

test('no sold-comps link without an identity (bad photo)', () => {
  const r: ScanResponse = { vision: { photo_ok: false, photo_issue: 'too much glare',
    identity: null, condition: null, authenticity: null, ai_value_note: null },
    comps: null, comps_error: null, verdict: null }
  renderResults(r)
  expect(screen.queryByRole('link', { name: /see sold comps on ebay/i })).toBeNull()
})

test('asks-vs-solds caption omitted when comps already come from solds', () => {
  const r = structuredClone(base)
  r.comps!.source = 'sold'
  renderResults(r)
  expect(screen.getByRole('link', { name: /see sold comps on ebay/i })).toBeTruthy()
  expect(screen.queryByText(/what buyers actually paid/i)).toBeNull()
})

// --- Verdict labels and guardrail tints ---

test('verdict hero shows human label, not raw enum value', () => {
  const r = structuredClone(base)
  r.verdict = { value_low: 60, value_high: 90, verdict: 'no_ask', reasoning: 'No asking price given.' }
  renderResults(r)
  expect(screen.getByText('Value estimate')).toBeTruthy()
  expect(screen.queryByText('no_ask')).toBeNull()
})

test('authenticity_risk verdict renders red hero alongside the risk banner', () => {
  const r = structuredClone(base)
  r.vision.authenticity = { red_flags: ['print pattern looks off'], risk: 'high' }
  r.verdict = { value_low: 60, value_high: 90, verdict: 'authenticity_risk',
                reasoning: 'Counterfeit red flags detected.' }
  const { container } = renderResults(r)
  expect(screen.getByText('Caution: authenticity')).toBeTruthy()
  expect(container.querySelector('.hero')!.className).toContain('verdict-hero-authenticity_risk')
  // Existing red risk banner still shows its detail — hero and banner agree.
  expect(screen.getByText(/print pattern looks off/)).toBeTruthy()
})

test('low_value verdict renders "too cheap to call" in a neutral hero', () => {
  const r = structuredClone(base)
  r.verdict = { value_low: 5, value_high: 8, verdict: 'low_value',
                reasoning: 'Commodity-priced card.' }
  const { container } = renderResults(r)
  expect(screen.getByText('Too cheap to call')).toBeTruthy()
  expect(container.querySelector('.hero')!.className).toContain('verdict-hero-low_value')
})

test('high_value verdict renders "verify first" in an amber hero', () => {
  const r = structuredClone(base)
  r.verdict = { value_low: 800, value_high: 1500, verdict: 'high_value',
                reasoning: 'High-value card — estimate only.' }
  const { container } = renderResults(r)
  expect(screen.getByText('High value — verify first')).toBeTruthy()
  expect(container.querySelector('.hero')!.className).toContain('verdict-hero-high_value')
})

// --- Slabs ---

test('slab response renders badge and no estimated grade range', () => {
  const r = structuredClone(base)
  r.vision.condition = null // the slab already graded it
  r.vision.slab = { company: 'PSA', grade: '9' }
  r.verdict = { value_low: 100, value_high: 150, verdict: 'fair',
                reasoning: 'PSA 9 copies range $100–$150 based on 4 listings.' }
  renderResults(r)
  const badge = screen.getByText('PSA 9 slab')
  expect(badge.className).toContain('slab-badge')
  // No "PSA X–Y" estimated-condition heading for a professionally graded card.
  expect(screen.queryByText(/PSA \d+(\.\d+)?–\d+/)).toBeNull()
})

test('slab hides the estimated grade range even if condition sneaks through', () => {
  // Defensive: if the model returns both slab and condition, the slab wins.
  const r = structuredClone(base)
  r.vision.slab = { company: 'BGS', grade: '9.5' }
  renderResults(r)
  expect(screen.getByText('BGS 9.5 slab')).toBeTruthy()
  expect(screen.queryByText(/PSA 6–8/)).toBeNull()
})

test('non-slab response shows no slab badge', () => {
  renderResults(base)
  expect(screen.queryByText(/slab/i)).toBeNull()
})
