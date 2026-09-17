import { useEffect, useState } from 'react'
import AuthGate from '../auth/AuthGate'
import { filterMine } from '../auth/collection'
import { useAuth } from '../auth/useAuth'
import { listBinder, refreshComps } from '../binderApi'
import type { BinderCard, CardRecord } from '../binderTypes'
import type { ScanResponse } from '../types'
import { verdictLabels } from '../labels'
import ResultsScreen from './ResultsScreen'

type Cursor = { start_author: string; start_permlink: string } | null
// Community = the whole shared feed. My Collection = the same feed narrowed
// to the signed-in user's hive_display_key (server-side via owner=, and again
// client-side so a stray record can never show up under "mine").
type Tab = 'community' | 'mine'

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
  const { user } = useAuth()
  const [tab, setTab] = useState<Tab>('community')
  const [cards, setCards] = useState<BinderCard[]>([])
  const [next, setNext] = useState<Cursor>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [detail, setDetail] = useState<BinderCard | null>(null)
  const [refreshNote, setRefreshNote] = useState<string | null>(null)

  // undefined = whole community; a key = one collector; null = signed-out
  // "mine" (nothing to fetch — the gate asks for sign-in instead).
  const owner: string | null | undefined =
    tab === 'mine' ? (user?.hive_display_key ?? null) : undefined

  async function load(cursor: Cursor, forOwner: string | undefined) {
    setLoading(true)
    setError(null)
    try {
      const page = forOwner
        ? await listBinder(cursor ?? undefined, forOwner)
        : await listBinder(cursor ?? undefined) // signed-out call unchanged
      setCards(prev => (cursor ? [...prev, ...page.cards] : page.cards))
      setNext(page.next)
    } catch {
      setError('Could not load The Binder. Check your connection.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (owner === null) { setCards([]); setNext(null); setLoading(false); return }
    load(null, owner)
  }, [owner])

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

  const scoped = owner ? filterMine(cards, owner) : cards
  const visible = query ? scoped.filter(c => matches(c.card, query)) : scoped
  const emptyText = query ? 'No cards match your search.'
    : tab === 'mine' ? 'Your collection is empty — publish a scan from History.'
    : 'The Binder is empty — publish your first card from History.'

  const tabs = (
    <div className="tabs" role="tablist" aria-label="Binder view">
      <button role="tab" aria-selected={tab === 'community'} onClick={() => setTab('community')}>
        Community
      </button>
      <button role="tab" aria-selected={tab === 'mine'} onClick={() => setTab('mine')}>
        My Collection
      </button>
    </div>
  )

  if (owner === null) {
    return (
      <div className="screen">
        {tabs}
        <AuthGate prompt="Sign in to see the cards you have published to The Binder.">
          {null}
        </AuthGate>
      </div>
    )
  }

  return (
    <div className="screen">
      {tabs}
      <input
        type="search"
        placeholder="Search card, set, year…"
        value={query}
        onChange={e => setQuery(e.target.value)}
        aria-label="Search The Binder"
      />
      {error && <p role="alert">{error}</p>}
      {!loading && !error && visible.length === 0 && <p>{emptyText}</p>}
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
        <button onClick={() => load(next, owner)}>Load more</button>
      )}
    </div>
  )
}
