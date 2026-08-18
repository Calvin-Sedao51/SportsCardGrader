import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../api'
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

export default function HistoryScreen({ onSelect }: Props) {
  const [staged, setStaged] = useState<StagedCard[] | null>(null)
  const [consentCard, setConsentCard] = useState<StagedCard | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(() => {
    listStaged().then(setStaged).catch(() => setStaged([]))
  }, [])
  useEffect(() => { refresh() }, [refresh])
  const jobs = usePublishPoller(staged, refresh)

  async function handlePublish(card: StagedCard) {
    setConsentCard(null)
    setError(null)
    try {
      const images = await getImages(card.record_id)
      if (!images) {
        throw new ApiError('The photos for this scan are gone — rescan the card to publish it.')
      }
      // Consent is durable: if the network drops here, the startup/online
      // sweep in usePublishPoller re-submits without asking again.
      await setStatus(card.record_id, { publishRequested: true })
      const job = await publishCard(card, images.front, images.back)
      await setStatus(card.record_id, {
        status: 'queued', job_id: job.job_id, permlink: job.permlink,
      })
    } catch (err) {
      setError(err instanceof ApiError ? err.message
        : 'Could not publish right now — will retry when you are back online.')
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

  return (
    <div className="screen">
      {error && (
        <p role="alert">
          {error} <button onClick={() => setError(null)}>Dismiss</button>
        </p>
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
