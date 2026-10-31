import { useEffect, useMemo, useRef, useState } from 'react'

interface Props {
  onSubmit: (front: File, back: File | null, askingPrice: number | null) => void
  busy: boolean
}

const PRESETS = [5, 10, 20, 50, 100]

// Stable preview URL per file; revoked when the file changes or on unmount.
function useObjectUrl(file: File | null) {
  const url = useMemo(() => (file ? URL.createObjectURL(file) : null), [file])
  useEffect(() => () => { if (url) URL.revokeObjectURL(url) }, [url])
  return url
}

const CameraIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M4 8h3l2-3h6l2 3h3v11H4z" />
    <circle cx="12" cy="13" r="3.5" />
  </svg>
)

export default function ScanScreen({ onSubmit, busy }: Props) {
  const [front, setFront] = useState<File | null>(null)
  const [back, setBack] = useState<File | null>(null)
  // Price is one value with two entry paths: a preset chip wins while selected;
  // otherwise the custom text (revealed by "Other…") is used.
  const [chip, setChip] = useState<number | null>(null)
  const [ask, setAsk] = useState('')
  const [customOpen, setCustomOpen] = useState(false)
  const customRef = useRef<HTMLInputElement>(null)
  const frontUrl = useObjectUrl(front)
  const backUrl = useObjectUrl(back)

  useEffect(() => {
    if (customOpen) customRef.current?.focus()
  }, [customOpen])

  const askingPrice = chip !== null ? chip : ask ? Number(ask) : null

  function toggleChip(value: number) {
    if (chip === value) {
      setChip(null)
    } else {
      setChip(value)
      setAsk('')
      setCustomOpen(false)
    }
  }

  return (
    <div className="screen">
      <label className="capture">
        {frontUrl ? (
          <>
            <img src={frontUrl} alt="card front" />
            <span className="capture-hint">Tap to retake</span>
          </>
        ) : (
          <>
            <CameraIcon />
            Tap to photograph card front
            {/* A bad photo still costs a paid AI call — coach before the shot. */}
            <span className="capture-hint">Lay it flat in good light and fill the frame</span>
          </>
        )}
        <input type="file" accept="image/*" capture="environment" hidden
               onChange={e => setFront(e.target.files?.[0] ?? null)} />
      </label>
      <label className="capture small">
        {backUrl ? <img src={backUrl} alt="card back" /> : '+ back (optional)'}
        <input type="file" accept="image/*" capture="environment" hidden
               onChange={e => setBack(e.target.files?.[0] ?? null)} />
      </label>
      <div className="price">
        <label htmlFor="ask-custom">Asking price (optional)</label>
        <div className="chips">
          {PRESETS.map(value => (
            <button key={value} type="button" className="chip"
                    aria-pressed={chip === value}
                    onClick={() => toggleChip(value)}>
              ${value}
            </button>
          ))}
          <button type="button" className="chip" aria-pressed={customOpen}
                  onClick={() => { setCustomOpen(true); setChip(null) }}>
            Other…
          </button>
        </div>
        <input id="ask-custom" ref={customRef} type="number" inputMode="decimal"
               min="0" placeholder="$" value={ask} hidden={!customOpen}
               onChange={e => { setAsk(e.target.value); setChip(null) }} />
      </div>
      <div className="scan-cta">
        {!front && <p className="caption">Add a front photo to scan</p>}
        <button disabled={!front || busy}
                onClick={() => onSubmit(front!, back, askingPrice)}>
          {busy ? 'Scanning…' : 'Scan card'}
        </button>
      </div>
    </div>
  )
}
