import { useEffect, useRef, useState } from 'react'
import { ApiError, checkHealth, SCAN_TIMEOUT_MS, scanCard } from './api'
import { prepareImage } from './imagePrep'
import ScanOverlay from './ScanOverlay'
import AuthButton from './auth/AuthButton'
import BinderScreen from './screens/BinderScreen'
import HistoryScreen from './screens/HistoryScreen'
import ResultsScreen from './screens/ResultsScreen'
import ScanScreen from './screens/ScanScreen'
import SettingsScreen from './screens/SettingsScreen'
import { migrateFromLocalStorage, stageScan } from './binderDb'
import { loadSettings, pushHistory } from './storage'
import type { ScanResponse } from './types'

type View = 'scan' | 'results' | 'settings' | 'history' | 'binder'

function App() {
  // First run: no saved settings yet, force onboarding.
  const [view, setView] = useState<View>(() => (loadSettings() ? 'scan' : 'settings'))
  const [lastResult, setLastResult] = useState<ScanResponse | null>(null)
  const [lastAskingPrice, setLastAskingPrice] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [serverDown, setServerDown] = useState(false)
  // Front-photo object URL shown in the wait overlay; revoked when the scan ends.
  const [scanPreviewUrl, setScanPreviewUrl] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  // Re-runs the health probe and syncs the banner. Returns the fresh result so
  // callers (pre-scan gate) can branch without waiting on a state read.
  async function recheckHealth(): Promise<boolean> {
    const ok = await checkHealth()
    setServerDown(!ok)
    return ok
  }

  useEffect(() => {
    checkHealth().then(ok => {
      if (!ok) setServerDown(true)
    })
    // One-time import of pre-IndexedDB localStorage history; harmless no-op after.
    migrateFromLocalStorage().catch(err =>
      console.warn('History migration failed:', err))
    // Show wifi drops constantly; when the browser regains a network path,
    // re-probe instead of leaving a stale "unreachable" banner up.
    const onOnline = () => {
      void checkHealth().then(ok => setServerDown(!ok))
    }
    window.addEventListener('online', onOnline)
    return () => window.removeEventListener('online', onOnline)
  }, [])

  async function handleScan(front: File, back: File | null, askingPrice: number | null) {
    const settings = loadSettings()
    if (!settings) {
      setView('settings')
      return
    }
    if (busy) return // health re-probe below can take seconds; block double-taps
    // Banner says the server is down: re-probe before burning an upload over a
    // dead link. If it recovered, the banner clears and the scan proceeds.
    if (serverDown && !(await recheckHealth())) return
    const controller = new AbortController()
    abortRef.current = controller
    // Combine user cancel with a hard client timeout. AbortSignal.any/timeout
    // are Baseline-available (Chrome 116+, Firefox 124+, Safari 17.4+); on older
    // browsers degrade to cancel-only rather than throwing before the try block.
    const signal =
      'any' in AbortSignal && 'timeout' in AbortSignal
        ? AbortSignal.any([controller.signal, AbortSignal.timeout(SCAN_TIMEOUT_MS)])
        : controller.signal
    const previewUrl = URL.createObjectURL(front)
    setScanPreviewUrl(previewUrl)
    setBusy(true)
    setError(null)
    try {
      // Downscale/re-encode to JPEG before upload; previews keep the originals.
      const [prepFront, prepBack] = await Promise.all([
        prepareImage(front),
        back ? prepareImage(back) : Promise.resolve(null),
      ])
      const response = await scanCard(prepFront, prepBack, askingPrice, settings, signal)
      // Show the paid-for result FIRST — the staging write is best-effort and
      // must never cost the seller a completed scan (e.g. storage full).
      setLastResult(response)
      setLastAskingPrice(askingPrice)
      setView('results')
      try {
        // Keep the prepared blobs: they're what gets published to The Binder.
        await stageScan(response, askingPrice, prepFront, prepBack)
      } catch (stageErr) {
        console.warn('Could not stage scan; falling back to localStorage:', stageErr)
        try {
          pushHistory({ at: new Date().toISOString(), response, askingPrice: askingPrice ?? undefined })
        } catch (histErr) {
          console.warn('Could not save scan to history:', histErr)
        }
      }
      navigator.vibrate?.(50)
    } catch (err) {
      if (err instanceof DOMException && (err.name === 'AbortError' || err.name === 'TimeoutError')) {
        // User cancel (controller fired) is silent; anything else abort-shaped
        // is the client-side timeout.
        if (!controller.signal.aborted) {
          setError('Scan timed out. Check your connection and try again.')
        }
      } else {
        setError(err instanceof ApiError ? err.message : 'Something went wrong. Try again.')
      }
    } finally {
      setBusy(false)
      abortRef.current = null
      setScanPreviewUrl(null)
      URL.revokeObjectURL(previewUrl)
    }
  }

  return (
    <div className="app">
      <header>
        <h1>Card Scanner</h1>
        <nav>
          {/* Disabled while scanning: navigating mid-scan would teleport the
              user away and orphan the in-flight result. */}
          <button disabled={busy} onClick={() => setView('scan')}>Scan</button>
          <button disabled={busy} onClick={() => setView('binder')}>Binder</button>
          <button disabled={busy} onClick={() => setView('history')}>History</button>
          <button disabled={busy} onClick={() => setView('settings')}>Settings</button>
        </nav>
        <AuthButton />
      </header>

      {serverDown && (
        <div className="banner" role="alert">
          Scan server unreachable
          <button onClick={() => void recheckHealth()}>Retry</button>
          <button onClick={() => setServerDown(false)}>Dismiss</button>
        </div>
      )}

      {view === 'settings' && <SettingsScreen onDone={() => setView('scan')} />}
      {view === 'scan' && (
        <>
          {error && (
            <p role="alert">
              {error} <button onClick={() => setError(null)}>Dismiss</button>
            </p>
          )}
          <ScanScreen onSubmit={handleScan} busy={busy} />
        </>
      )}
      {view === 'results' && lastResult && (
        <ResultsScreen
          result={lastResult}
          askingPrice={lastAskingPrice}
          onRescan={() => setView('scan')}
        />
      )}
      {busy && (
        <ScanOverlay previewUrl={scanPreviewUrl} onCancel={() => abortRef.current?.abort()} />
      )}

      {view === 'binder' && <BinderScreen />}

      {view === 'history' && (
        <HistoryScreen
          onSelect={e => {
            setLastResult(e.response)
            setLastAskingPrice(e.askingPrice ?? null)
            setView('results')
          }}
        />
      )}
    </div>
  )
}

export default App
