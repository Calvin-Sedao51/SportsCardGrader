import { useEffect, useState } from 'react'

interface Props {
  /** Object URL of the front photo (owned and revoked by the caller). */
  previewUrl: string | null
  onCancel: () => void
}

// Staged copy keeps a 5-15s AI wait feeling alive: each line names what the
// pipeline is plausibly doing right now. The last stage holds indefinitely.
const STAGES: { atMs: number; text: string }[] = [
  { atMs: 0, text: 'Reading the card…' },
  { atMs: 4000, text: 'Estimating condition…' },
  { atMs: 8000, text: 'Checking recent sales…' },
]

export default function ScanOverlay({ previewUrl, onCancel }: Props) {
  const [stage, setStage] = useState(0)

  useEffect(() => {
    const timers = STAGES.slice(1).map((s, i) =>
      setTimeout(() => setStage(i + 1), s.atMs))
    return () => timers.forEach(clearTimeout)
  }, [])

  // Escape cancels the scan, matching the visible Cancel button.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])

  return (
    <div className="scan-overlay" role="status">
      <div className="scan-overlay-card">
        {previewUrl && (
          <img className="scan-overlay-thumb" src={previewUrl} alt="card being scanned" />
        )}
        <div className="spinner" aria-hidden="true" />
        <p className="scan-overlay-stage">{STAGES[stage].text}</p>
        <p className="caption">usually takes 10–20 seconds</p>
        <button className="scan-overlay-cancel" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}
