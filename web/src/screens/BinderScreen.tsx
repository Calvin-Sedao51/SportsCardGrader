import { useEffect, useState } from 'react'
import { listBinder, refreshComps } from '../binderApi'
import type { BinderCard, CardRecord } from '../binderTypes'
import type { ScanResponse } from '../types'
import { verdictLabels } from '../labels'
import ResultsScreen from './ResultsScreen'

type Cursor = { start_author: string; start_permlink: string } | null

// A published record renders through the same components as a live scan.
function toScanResponse(card: CardRecord): ScanResponse {
  return {
    vision: {
      photo_ok: true,
      photo_issue: null,
      identity: card.identity,
      condition: card.condition,
      slab: card.slab,
      authenticity: card.authenticity,
      ai_value_note: null,
    },
    comps: card.comps?.summary ?? null,
    comps_error: null,
    verdict: card.verdict,
  }
}

function matches(card: CardRecord, query: string): boolean {
  const haystack = [card.identity.subject, card.identity.set_name, card.identity.year]
    .join(' ').toLowerCase()
  return haystack.includes(query.toLowerCase())
}

export default function BinderScreen() {
  const [cards, setCards] = useState<BinderCard[]>([])
  const [next, setNext] = useState<Cursor>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [detail, setDetail] = useState<BinderCard | null>(null)
  const [refreshNote, setRefreshNote] = useState<string | null>(null)

  async function load(cursor: Cursor) {
    setLoading(true)
    setError(null)
    try {
      const page = await listBinder(cursor ?? undefined)
      setCards(prev => (cursor ? [...prev, ...page.cards] : page.cards))
      setNext(page.next)
    } catch {
      setError('Could not load The Binder. Check your connection.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load(null) }, [])

  async function handleRefreshComps(permlink: string) {
    setRefreshNote(null)
    try {
      await refreshComps(permlink)
      setRefreshNote('Comps refresh queued — the post updates in a few minutes.')
    } catch {
      setRefreshNote('Could not queue the refresh. Try again later.')
    }
  }

  if (detail) {
    const hiveUrl = `https://peakd.com/@${detail.author}/${detail.permlink}`
    return (
      <div className="screen binder-detail">
        <button onClick={() => { setDetail(null); setRefreshNote(null) }}>← Back to The Binder</button>
        <img className="binder-photo" src={detail.card.images.front} alt="card front" />
        {detail.card.images.back && (
          <img className="binder-photo" src={detail.card.images.back} alt="card back" />
        )}
        <ResultsScreen result={toScanResponse(detail.card)} onRescan={() => setDetail(null)} />
        <div className="binder-detail-actions">
          <a href={hiveUrl} target="_blank" rel="noopener noreferrer">View on Hive</a>
          <button onClick={() => handleRefreshComps(detail.permlink)}>Refresh comps</button>
        </div>
        {refreshNote && <p className="caption">{refreshNote}</p>}
      </div>
    )
  }

  const visible = query ? cards.filter(c => matches(c.card, query)) : cards

  return (
    <div className="screen">
      <input
        type="search"
        placeholder="Search card, set, year…"
        value={query}
        onChange={e => setQuery(e.target.value)}
        aria-label="Search The Binder"
      />
      {error && <p role="alert">{error}</p>}
      {!loading && !error && visible.length === 0 && (
        <p>{query ? 'No cards match your search.' : 'The Binder is empty — publish your first card from History.'}</p>
      )}
      <ul className="history">
        {visible.map(item => (
          <li key={`${item.author}/${item.permlink}`}>
            <button className="history-row" onClick={() => setDetail(item)}>
              <span className="player">
                {item.card.identity.subject}
                {item.card.slab ? ` · ${item.card.slab.company} ${item.card.slab.grade}` : ''}
              </span>
              <span className="date">
                {item.card.identity.year} {item.card.identity.set_name}
              </span>
              {item.card.verdict && (
                <span className={`verdict verdict-${item.card.verdict.verdict}`}>
                  {verdictLabels[item.card.verdict.verdict]}
                </span>
              )}
              {item.card.asking_price != null && (
                <span className="ask">${item.card.asking_price}</span>
              )}
            </button>
          </li>
        ))}
      </ul>
      {loading && <p>Loading…</p>}
      {next && !loading && (
        <button onClick={() => load(next)}>Load more</button>
      )}
    </div>
  )
}
