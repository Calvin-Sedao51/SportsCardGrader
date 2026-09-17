import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../api'
import { useAuth } from '../auth/useAuth'
import { publishCard } from '../binderApi'
import { getImages, listStaged, setStatus } from '../binderDb'
import type { StagedCard } from '../binderTypes'
import { verdictLabels } from '../labels'
import { usePublishPoller } from '../usePublishPoller'

interface Props {
  onSelect: (entry: StagedCard) => void
}

function chipLabel(card: StagedCard, position?: number): string {
  switch (card.status) {
    case 'draft': return card.legacy ? 'Local only' : 'Draft'
    case 'queued': return position ? `In queue (#${position})` : 'In queue'
    case 'publishing': return 'Publishing…'
    case 'published': return 'Published'
    case 'failed': return 'Failed'
  }
}

function isPublishable(card: StagedCard): boolean {
  return card.status === 'draft' && !card.legacy && card.response.vision.identity != null
}

export default function HistoryScreen({ onSelect }: Props) {
  const { user } = useAuth()
  const [staged, setStaged] = useState<StagedCard[] | null>(null)
  const [consentCard, setConsentCard] = useState<StagedCard | null>(null)
  const [syncConsent, setSyncConsent] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(() => {
    listStaged().then(setStaged).catch(() => setStaged([]))
  }, [])
  useEffect(() => { refresh() }, [refresh])
  const jobs = usePublishPoller(staged, refresh)

  async function publishOne(card: StagedCard): Promise<void> {
    const images = await getImages(card.record_id)
    if (!images) {
      throw new ApiError('The photos for this scan are gone — rescan the card to publish it.')
    }
    // Publishing requires sign-in: never mark a signed-out scan for
    // auto-resubmit — it would publish after a later sign-in without a
    // fresh consent tap.
    if (!user) {
      throw new ApiError('Sign in with Google to publish to the Hive.')
    }
    // Consent is durable: if the network drops here, the startup/online
    // sweep in usePublishPoller re-submits without asking again. The user id
    // is attached to the staged scan so the sync survives a reload too.
    await setStatus(card.record_id, { publishRequested: true, user_id: user.id })
    try {
      const job = await publishCard(card, images.front, images.back)
      await setStatus(card.record_id, {
        status: 'queued', job_id: job.job_id, permlink: job.permlink,
      })
    } catch (err) {
      if (err instanceof ApiError && err.message.includes('hourly publish limit')) {
        throw new ApiError(err.message + ' (your consent is saved — it will publish automatically once the pause lifts)')
      }
      throw err
    }
  }

  async function handlePublish(card: StagedCard) {
    setConsentCard(null)
    setError(null)
    try {
      await publishOne(card)
    } catch (err) {
      setError(err instanceof ApiError ? err.message
        : 'Could not publish right now — will retry when you are back online.')
    }
    refresh()
  }

  // Scans made before signing in stay private in IndexedDB until the user
  // explicitly syncs them: one consent for the batch, then the normal
  // publish flow per card (attributed via the app token).
  async function handleSync(cards: StagedCard[]) {
    setSyncConsent(false)
    setError(null)
    for (const card of cards) {
      try {
        await publishOne(card)
      } catch (err) {
        setError(err instanceof ApiError ? err.message
          : 'Could not publish right now — will retry when you are back online.')
      }
    }
    refresh()
  }

  if (staged === null) return <div className="screen"><p>Loading…</p></div>
  if (staged.length === 0) {
    return (
      <div className="screen">
        <p>No scans yet.</p>
      </div>
    )
  }

  const unsynced = user ? staged.filter(isPublishable) : []

  return (
    <div className="screen">
      {error && (
        <p role="alert">
          {error} <button onClick={() => setError(null)}>Dismiss</button>
        </p>
      )}
      {unsynced.length > 0 && (
        <div className="sync-banner">
          <span>{unsynced.length} scan{unsynced.length === 1 ? '' : 's'} not in your collection yet.</span>
          <button onClick={() => setSyncConsent(true)}>Sync my scans</button>
        </div>
      )}
      <ul className="history">
        {staged.map(card => {
          const verdict = card.response.verdict
          const slab = card.response.vision.slab
          const publishable = card.status === 'draft' && !card.legacy
            && card.response.vision.identity != null
          return (
            <li key={card.record_id}>
              <button className="history-row" onClick={() => onSelect(card)}>
                <span className="player">
                  {card.response.vision.identity?.subject ?? 'Unreadable photo'}
                  {slab ? ` · ${slab.company} ${slab.grade}` : ''}
                </span>
                <span className="date">{new Date(card.at).toLocaleDateString()}</span>
                {verdict && (
                  <span className={`verdict verdict-${verdict.verdict}`}>{verdictLabels[verdict.verdict]}</span>
                )}
                {card.askingPrice != null && <span className="ask">${card.askingPrice}</span>}
              </button>
              <div className="history-actions">
                <span className={`chip chip-${card.status}`}>
                  {chipLabel(card, jobs[card.record_id]?.position)}
                </span>
                {publishable && (
                  <button onClick={() => setConsentCard(card)}>Publish to The Binder</button>
                )}
                {card.status === 'failed' && card.error && (
                  <span className="chip-error">{card.error}</span>
                )}
                {card.hive_url && (
                  <a href={card.hive_url} target="_blank" rel="noreferrer">View on Hive</a>
                )}
              </div>
            </li>
          )
        })}
      </ul>

      {syncConsent && (
        <div className="modal" role="dialog" aria-label="Sync my scans">
          <p>
            This publishes {unsynced.length} scan{unsynced.length === 1 ? '' : 's'} — photos,
            grade estimates, price data — to The Binder, a <strong>public</strong> community on
            the Hive blockchain, attributed to {user?.display_name}. Published posts are{' '}
            <strong>permanent</strong> and cannot be fully deleted.
          </p>
          <button onClick={() => handleSync(unsynced)}>Sync and publish</button>
          <button onClick={() => setSyncConsent(false)}>Cancel</button>
        </div>
      )}

      {consentCard && (
        <div className="modal" role="dialog" aria-label="Publish to The Binder">
          <p>
            Publishing posts this card — photos, grade estimate, price data —
            to The Binder, a <strong>public</strong> community on the Hive
            blockchain. Published posts are <strong>permanent</strong> and
            cannot be fully deleted.
          </p>
          <button onClick={() => handlePublish(consentCard)}>Publish</button>
          <button onClick={() => setConsentCard(null)}>Cancel</button>
        </div>
      )}
    </div>
  )
}
