// Mirrors server/app/schemas.py field-for-field (snake_case, matching serialized JSON).

export type CardCategory =
  | 'sports'
  | 'pokemon'
  | 'yugioh'
  | 'magic'
  | 'onepiece'
  | 'other_tcg'
  | 'other'

export interface Identity {
  subject: string // player (sports) or card/character name (TCG)
  category?: CardCategory // optional: staged scans from before this field lack it
  year: string
  set_name: string
  card_number: string | null
  variant: string | null
  search_string: string
  confidence: number // 0..1
}

export type Area = 'corners' | 'edges' | 'surface' | 'centering'
export type Severity = 'none' | 'minor' | 'moderate' | 'heavy'

export interface AreaObservation {
  area: Area
  severity: Severity
  note: string
}

export interface Condition {
  observations: AreaObservation[]
  grade_low: number // 1..10, half grades allowed, <= grade_high
  grade_high: number // 1..10
}

export type Risk = 'low' | 'caution' | 'high'

export interface Authenticity {
  red_flags: string[]
  risk: Risk
}

export type SlabCompany = 'PSA' | 'BGS' | 'SGC' | 'CGC' | 'TAG'

export interface Slab {
  company: SlabCompany
  grade: string // "9", "9.5", "10", ... — string because "Authentic" slabs exist
}

export interface VisionResult {
  photo_ok: boolean
  photo_issue: string | null // auto-filled server-side when photo_ok is false
  identity: Identity | null
  condition: Condition | null // null for slabbed cards — the slab already graded it
  // Optional (not `| null`) so history entries saved before slab support still type-check.
  slab?: Slab | null
  authenticity: Authenticity | null
  ai_value_note: string | null // model's rough value memory; fallback when comps are empty
}

export interface CompListing {
  title: string
  price: number // >= 0
  graded: boolean
  grade_label: string | null
  url: string | null
}

export type CompsSource = 'active_listings' | 'sold'

export interface CompsSummary {
  source: CompsSource
  raw_count: number // >= 0
  raw_low: number | null
  raw_median: number | null
  graded_count: number // >= 0
  graded_low: number | null
  graded_median: number | null
}

export type VerdictLabel =
  | 'undervalued'
  | 'fair'
  | 'overpriced'
  | 'no_ask'
  | 'not_enough_data'
  | 'authenticity_risk'
  | 'low_value'
  | 'high_value'

export interface Verdict {
  value_low: number | null
  value_high: number | null
  verdict: VerdictLabel
  reasoning: string
}

export interface ScanResponse {
  vision: VisionResult
  comps: CompsSummary | null
  comps_error: string | null
  verdict: Verdict | null
}

// Client-only types

export type Provider = 'anthropic' | 'openai'

export interface AiSettings {
  provider: Provider
  apiKey: string
  model?: string
}

export interface HistoryEntry {
  at: string
  response: ScanResponse
  askingPrice?: number
}
