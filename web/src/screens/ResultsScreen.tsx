import { verdictLabels } from '../labels'
import type { CardCategory, CompsSource, ScanResponse } from '../types'

interface Props {
  result: ScanResponse
  onRescan: () => void
}

// 6 -> "6", 6.5 -> "6.5" (never "6.0" — no forced decimal place).
const fmt = (n: number) => String(n)

const sourceCaption: Record<CompsSource, string> = {
  active_listings: 'based on current eBay asking prices',
  sold: 'based on eBay sold prices',
}

// eBay category ids for the sold-comps deep link, mapped by card category.
// Keep in sync with EBAY_CATEGORY in server/app/ebay.py.
const SACAT_BY_CATEGORY: Partial<Record<CardCategory, string>> = {
  sports: '212', // Sports Trading Cards
  pokemon: '183454', // CCG Individual Cards
  yugioh: '183454',
  magic: '183454',
  onepiece: '183454',
  other_tcg: '183454',
}

// Deep link to eBay's completed-and-sold search in the category-mapped id:
// solds are ground truth on eBay, one tap away from our ask-based estimate.
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

export default function ResultsScreen({ result, onRescan }: Props) {
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

  return (
    <div className="screen">
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

      {authenticity && (
        <section className={`authenticity risk-${authenticity.risk}`}>
          {authenticity.risk === 'low' && authenticity.red_flags.length === 0 ? (
            <p className="subtle">No counterfeit red flags spotted</p>
          ) : (
            <>
              <p>Counterfeit risk: {authenticity.risk}</p>
              {authenticity.red_flags.length > 0 && (
                <ul>
                  {authenticity.red_flags.map(flag => (
                    <li key={flag}>{flag}</li>
                  ))}
                </ul>
              )}
            </>
          )}
        </section>
      )}

      {/* No estimated grade range for a slab — the label already graded it. */}
      {condition && !slab && (
        <section className="grade">
          <h3>
            PSA {fmt(condition.grade_low)}–{fmt(condition.grade_high)}
          </h3>
          {condition.observations.length > 0 && (
            <ul>
              {condition.observations.map((o, i) => (
                <li key={`${o.area}-${i}`}>
                  {o.area} — {o.severity}: {o.note}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      <section className="value">
        {verdict ? (
          <>
            <span className={`verdict verdict-${verdict.verdict}`}>{verdictLabels[verdict.verdict]}</span>
            {verdict.value_low != null && verdict.value_high != null && (
              <p className="range">
                ${verdict.value_low}–${verdict.value_high}
              </p>
            )}
            <p>{verdict.reasoning}</p>
            {comps && <p className="caption">{sourceCaption[comps.source]}</p>}
            {identity && (
              <>
                {/* The source caption above already says the estimate is ask-based;
                    this line only adds what solds are. Skip it when comps ARE solds. */}
                {comps?.source === 'active_listings' && (
                  <p className="caption">Sold prices show what buyers actually paid.</p>
                )}
                <SoldCompsLink searchString={identity.search_string} category={identity.category} />
              </>
            )}
          </>
        ) : (
          <>
            <p>Market value unavailable</p>
            {comps_error && <p className="caption">{comps_error}</p>}
            {ai_value_note && (
              <p className="caption">AI rough estimate (low confidence): {ai_value_note}</p>
            )}
            {identity && (
              <SoldCompsLink searchString={identity.search_string} category={identity.category} />
            )}
          </>
        )}
      </section>

      <button onClick={onRescan}>Scan another</button>
    </div>
  )
}
