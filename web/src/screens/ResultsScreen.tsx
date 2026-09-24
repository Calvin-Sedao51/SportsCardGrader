import { verdictLabels } from '../labels'
import type { CardCategory, CompsSummary, ScanResponse, Verdict } from '../types'

interface Props {
  result: ScanResponse
  askingPrice: number | null
  onRescan: () => void
}

// 6 -> "6", 6.5 -> "6.5" (never "6.0" — no forced decimal place).
const fmt = (n: number) => String(n)

// Deep link to eBay's completed-and-sold search, mapped by card category:
// solds are ground truth on eBay, one tap away from our ask-based estimate.
// Keep in sync with EBAY_CATEGORY in server/app/ebay.py.
const SACAT_BY_CATEGORY: Partial<Record<CardCategory, string>> = {
  sports: '212', // Sports Trading Cards
  pokemon: '183454', // CCG Individual Cards
  yugioh: '183454',
  magic: '183454',
  onepiece: '183454',
  other_tcg: '183454',
}

function SoldCompsLink({ searchString, category }: { searchString: string; category?: CardCategory }) {
  const sacat = SACAT_BY_CATEGORY[category ?? 'sports']
  const href =
    `https://www.ebay.com/sch/i.html?_nkw=${encodeURIComponent(searchString)}` +
    (sacat ? `&_sacat=${sacat}` : '') +
    '&LH_Sold=1&LH_Complete=1'
  return (
    <a className="sold-link" href={href} target="_blank" rel="noopener noreferrer">
      See sold comps on eBay <span aria-hidden="true">→</span>
    </a>
  )
}

// Evidence caption: real counts beat a generic source line. Slab scans price
// against graded comps only (raw_count 0), so credit the graded pool then.
function evidenceCaption(comps: CompsSummary): string | null {
  if (comps.source === 'sold') return `Based on ${comps.raw_count} sold listings`
  if (comps.raw_count === 0) {
    return comps.graded_count > 0 ? `Based on ${comps.graded_count} graded listings` : null
  }
  const graded = comps.graded_count > 0 ? `, ${comps.graded_count} graded` : ''
  return `Based on ${comps.raw_count} current listings${graded}`
}

// The money line: how far the ask sits from the comps midpoint. Only rendered
// for price-call verdicts — guardrail verdicts have no meaningful delta.
function DeltaLine({ verdict, askingPrice }: { verdict: Verdict; askingPrice: number | null }) {
  if (askingPrice == null) return null
  if (verdict.verdict === 'fair') {
    // The supporting line below already shows "· asking $N" — don't repeat it.
    return <p className="delta">In range</p>
  }
  if (verdict.verdict !== 'undervalued' && verdict.verdict !== 'overpriced') return null
  if (verdict.value_low == null || verdict.value_high == null) return null
  const midpoint = (verdict.value_low + verdict.value_high) / 2
  const delta = Math.abs(Math.round(midpoint - askingPrice))
  return (
    <p className="delta">
      ≈ ${delta} {verdict.verdict === 'undervalued' ? 'below' : 'above'} market
    </p>
  )
}

export default function ResultsScreen({ result, askingPrice, onRescan }: Props) {
  const { vision, comps, comps_error, verdict } = result

  if (!vision.photo_ok) {
    return (
      <div className="screen">
        <p role="alert">
          Photo problem: {vision.photo_issue ?? 'the photo could not be read'}
        </p>
        <button onClick={onRescan}>Retake</button>
      </div>
    )
  }

  const { identity, condition, slab, authenticity, ai_value_note } = vision
  const caption = comps ? evidenceCaption(comps) : null

  return (
    <div className="screen">
      {/* Risk keeps veto placement: a non-low risk banner sits above the money moment. */}
      {authenticity && authenticity.risk !== 'low' && (
        <section className={`authenticity risk-${authenticity.risk}`}>
          <p>Counterfeit risk: {authenticity.risk}</p>
          {authenticity.red_flags.length > 0 && (
            <ul>
              {authenticity.red_flags.map(flag => (
                <li key={flag}>{flag}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      {/* Verdict hero: the product's money moment, first on screen. */}
      <section className={`hero verdict-hero-${verdict ? verdict.verdict : 'neutral'}`}>
        {verdict ? (
          <>
            <p className="verdict-label">{verdictLabels[verdict.verdict]}</p>
            <DeltaLine verdict={verdict} askingPrice={askingPrice} />
            {verdict.value_low != null && verdict.value_high != null && (
              <p className="range">
                Est. ${Math.round(verdict.value_low)}–${Math.round(verdict.value_high)}
                {askingPrice != null ? ` · asking $${Math.round(askingPrice)}` : ''}
              </p>
            )}
            <p>{verdict.reasoning}</p>
            {caption && <p className="caption">{caption}</p>}
          </>
        ) : (
          <>
            {/* Comps failed: the hero stays, neutral, and owns the fallback story. */}
            <p className="verdict-label">Market value unavailable</p>
            {comps_error && <p className="caption">{comps_error}</p>}
            {ai_value_note && (
              <p className="caption">AI rough estimate (low confidence): {ai_value_note}</p>
            )}
          </>
        )}
        {authenticity?.risk === 'low' && (
          <p className="check-line">✓ No counterfeit red flags</p>
        )}
        {identity && (
          <>
            {/* The evidence caption above already says the estimate is ask-based;
                this line only adds what solds are. Skip it when comps ARE solds. */}
            {verdict && comps?.source === 'active_listings' && (
              <p className="caption">Sold prices show what buyers actually paid.</p>
            )}
            <SoldCompsLink searchString={identity.search_string} category={identity.category} />
          </>
        )}
      </section>

      {identity && (
        <section className="identity">
          <h2>{identity.subject}</h2>
          <p>
            {identity.year} {identity.set_name}
            {identity.card_number ? ` #${identity.card_number}` : ''}
          </p>
          {identity.variant && <p className="variant">{identity.variant}</p>}
          {identity.confidence < 0.5 && (
            <p className="warning">Identification uncertain — price may not be reliable</p>
          )}
        </section>
      )}

      {slab && (
        <span className="slab-badge">
          {slab.company} {slab.grade} slab
        </span>
      )}

      {/* No estimated grade range for a slab — the label already graded it. */}
      {condition && !slab && (
        <section className="grade">
          <span className="grade-chip">
            PSA {fmt(condition.grade_low)}–{fmt(condition.grade_high)} (est.)
          </span>
          {condition.observations.length > 0 && (
            <details className="observations">
              <summary>What the AI saw</summary>
              <ul>
                {condition.observations.map((o, i) => (
                  <li key={`${o.area}-${i}`}>
                    {o.area} — {o.severity}: {o.note}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </section>
      )}

      <button onClick={onRescan}>Scan another</button>
    </div>
  )
}
