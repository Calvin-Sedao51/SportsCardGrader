import { render, screen, fireEvent, waitFor, waitForElementToBeRemoved } from '@testing-library/react'
import { beforeAll, beforeEach, expect, test, vi } from 'vitest'
import App from './App'
import { ApiError } from './api'
import { loadHistory, saveSettings } from './storage'
import type { ScanResponse } from './types'

const mocks = vi.hoisted(() => ({
  scanCard: vi.fn(),
  prepareImage: vi.fn(),
  pushHistory: vi.fn(),
  stageScan: vi.fn(),
  migrateFromLocalStorage: vi.fn(),
  listStaged: vi.fn(),
  checkHealth: vi.fn(),
}))

// checkHealth defaults to healthy (no banner); individual tests flip it to
// exercise the serverDown banner and recheck paths.
vi.mock('./api', async importOriginal => ({
  ...(await importOriginal<typeof import('./api')>()),
  checkHealth: () => mocks.checkHealth(),
  scanCard: mocks.scanCard,
}))

// Real prepareImage needs canvas/createImageBitmap (absent in jsdom); pass through.
vi.mock('./imagePrep', () => ({ prepareImage: mocks.prepareImage }))

// pushHistory is swappable per-test (the history-write guard test makes it throw);
// by default it delegates to the real implementation so history assertions hold.
vi.mock('./storage', async importOriginal => ({
  ...(await importOriginal<typeof import('./storage')>()),
  pushHistory: (...args: unknown[]) => mocks.pushHistory(...args),
}))
const realStorage = await vi.importActual<typeof import('./storage')>('./storage')

// IndexedDB staging is unit-tested in binderDb.test.ts; here we only assert
// the scan flow hands results to it (and falls back when it throws).
vi.mock('./binderDb', async importOriginal => ({
  ...(await importOriginal<typeof import('./binderDb')>()),
  stageScan: (...args: unknown[]) => mocks.stageScan(...args),
  migrateFromLocalStorage: (...args: unknown[]) => mocks.migrateFromLocalStorage(...args),
  listStaged: (...args: unknown[]) => mocks.listStaged(...args),
}))

// jsdom does not implement object URLs; the front-photo preview creates and revokes them.
beforeAll(() => {
  URL.createObjectURL = vi.fn(() => 'blob:mock')
  URL.revokeObjectURL = vi.fn()
})

beforeEach(() => {
  localStorage.clear()
  mocks.scanCard.mockReset()
  mocks.prepareImage.mockReset().mockImplementation((f: File) => Promise.resolve(f))
  mocks.pushHistory.mockReset().mockImplementation(realStorage.pushHistory)
  mocks.stageScan.mockReset().mockResolvedValue({ record_id: 'staged', status: 'draft' })
  mocks.migrateFromLocalStorage.mockReset().mockResolvedValue(0)
  mocks.listStaged.mockReset().mockResolvedValue([])
  mocks.checkHealth.mockReset().mockResolvedValue(true)
})

const stubResponse: ScanResponse = {
  vision: {
    photo_ok: true,
    photo_issue: null,
    identity: null,
    condition: null,
    authenticity: null,
    ai_value_note: null,
  },
  comps: null,
  comps_error: null,
  verdict: null,
}

function pickFrontAndScan() {
  fireEvent.change(screen.getByLabelText(/photograph card front/i), {
    target: { files: [new File(['x'], 'f.jpg', { type: 'image/jpeg' })] },
  })
  fireEvent.click(screen.getByRole('button', { name: /scan card/i }))
}

test('renders header title', () => {
  render(<App />)
  expect(screen.getByRole('heading', { name: 'Card Scanner' })).toBeDefined()
})

test('first run forces settings view when no settings saved', () => {
  render(<App />)
  expect(screen.getByText(/bring your own ai/i)).toBeDefined()
})

test('shows scan view when settings exist, nav switches views', () => {
  saveSettings({ provider: 'anthropic', apiKey: 'sk-test' })
  render(<App />)
  expect(screen.getByText(/photograph card front/i)).toBeDefined()
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
  expect(screen.getByText(/bring your own ai/i)).toBeDefined()
  fireEvent.click(screen.getByRole('button', { name: 'Scan' }))
  expect(screen.getByText(/photograph card front/i)).toBeDefined()
})

test('successful scan stages the card with its prepared images', async () => {
  saveSettings({ provider: 'anthropic', apiKey: 'sk-test' })
  mocks.scanCard.mockResolvedValue(stubResponse)
  render(<App />)
  pickFrontAndScan()
  // Real ResultsScreen: stubResponse has no verdict, so value is unavailable.
  await screen.findByText(/market value unavailable/i)
  expect(screen.getByRole('button', { name: /scan another/i })).toBeDefined()
  expect(mocks.stageScan).toHaveBeenCalledWith(
    stubResponse, null, mocks.prepareImage.mock.calls[0][0], null)
  expect(loadHistory()).toHaveLength(0) // localStorage is only the fallback now
  expect(mocks.scanCard).toHaveBeenCalledOnce()
})

test('binder tab shows the community feed screen', async () => {
  saveSettings({ provider: 'anthropic', apiKey: 'sk-test' })
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ cards: [], next: null }), { status: 200 }))
  render(<App />)
  fireEvent.click(screen.getByRole('button', { name: 'Binder' }))
  expect(await screen.findByLabelText(/search the binder/i)).toBeDefined()
})

test('history migration runs once on app start', () => {
  saveSettings({ provider: 'anthropic', apiKey: 'sk-test' })
  render(<App />)
  expect(mocks.migrateFromLocalStorage).toHaveBeenCalledOnce()
})

test('images are prepared before upload; scanCard gets the prepared files', async () => {
  saveSettings({ provider: 'anthropic', apiKey: 'sk-test' })
  mocks.scanCard.mockResolvedValue(stubResponse)
  const rawFront = new File(['raw-front'], 'front.heic', { type: 'image/heic' })
  const rawBack = new File(['raw-back'], 'back.heic', { type: 'image/heic' })
  const prepFront = new File(['prep-front'], 'front.jpg', { type: 'image/jpeg' })
  const prepBack = new File(['prep-back'], 'back.jpg', { type: 'image/jpeg' })
  mocks.prepareImage.mockImplementation((f: File) =>
    Promise.resolve(f === rawFront ? prepFront : prepBack))

  render(<App />)
  fireEvent.change(screen.getByLabelText(/photograph card front/i), {
    target: { files: [rawFront] },
  })
  fireEvent.change(screen.getByLabelText(/back \(optional\)/i), {
    target: { files: [rawBack] },
  })
  fireEvent.click(screen.getByRole('button', { name: /scan card/i }))

  await screen.findByText(/market value unavailable/i)
  expect(mocks.prepareImage).toHaveBeenCalledTimes(2)
  expect(mocks.prepareImage).toHaveBeenCalledWith(rawFront)
  expect(mocks.prepareImage).toHaveBeenCalledWith(rawBack)
  expect(mocks.scanCard).toHaveBeenCalledWith(
    prepFront, prepBack, null, expect.anything(), expect.any(AbortSignal))
})

test('front-only scan prepares just the front image', async () => {
  saveSettings({ provider: 'anthropic', apiKey: 'sk-test' })
  mocks.scanCard.mockResolvedValue(stubResponse)
  render(<App />)
  pickFrontAndScan()
  await screen.findByText(/market value unavailable/i)
  expect(mocks.prepareImage).toHaveBeenCalledTimes(1)
  expect(mocks.scanCard).toHaveBeenCalledWith(
    mocks.prepareImage.mock.calls[0][0], null, null, expect.anything(),
    expect.any(AbortSignal))
})

test('submit without settings redirects to settings view', () => {
  // No saved settings: App opens on settings; user navigates to scan anyway.
  render(<App />)
  fireEvent.click(screen.getByRole('button', { name: 'Scan' }))
  pickFrontAndScan()
  expect(screen.getByText(/bring your own ai/i)).toBeDefined()
  expect(mocks.scanCard).not.toHaveBeenCalled()
})

test('scan overlay shows while busy; cancel aborts with no error, stays on scan', async () => {
  saveSettings({ provider: 'anthropic', apiKey: 'sk-test' })
  // Hang until aborted, then reject the way fetch does on controller.abort().
  mocks.scanCard.mockImplementation((...args: unknown[]) => {
    const signal = args[4] as AbortSignal
    return new Promise((_, reject) =>
      signal.addEventListener('abort', () =>
        reject(new DOMException('The user aborted a request.', 'AbortError'))))
  })
  render(<App />)
  pickFrontAndScan()

  const overlay = await screen.findByRole('status')
  expect(overlay).toBeDefined()
  fireEvent.click(screen.getByRole('button', { name: /cancel/i }))

  // Busy clears: overlay gone, scan button back to idle, and no error alert.
  await waitForElementToBeRemoved(() => screen.queryByRole('status'))
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.getByRole('button', { name: /scan card/i })).toBeDefined()
})

test('timed-out scan shows a friendly timeout message', async () => {
  saveSettings({ provider: 'anthropic', apiKey: 'sk-test' })
  // AbortSignal.timeout() aborts with a DOMException named TimeoutError.
  mocks.scanCard.mockRejectedValue(new DOMException('signal timed out', 'TimeoutError'))
  render(<App />)
  pickFrontAndScan()
  const alert = await screen.findByRole('alert')
  expect(alert.textContent).toContain('Scan timed out. Check your connection and try again.')
  expect(screen.getByRole('button', { name: /scan card/i })).toBeDefined()
})

test('staging failure falls back to localStorage history', async () => {
  saveSettings({ provider: 'anthropic', apiKey: 'sk-test' })
  mocks.scanCard.mockResolvedValue(stubResponse)
  mocks.stageScan.mockRejectedValue(new Error('IndexedDB unavailable'))
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
  render(<App />)
  pickFrontAndScan()
  await screen.findByText(/market value unavailable/i)
  expect(loadHistory()).toHaveLength(1)
  expect(warn).toHaveBeenCalled()
  warn.mockRestore()
})

test('result still renders when staging AND the fallback write throw', async () => {
  saveSettings({ provider: 'anthropic', apiKey: 'sk-test' })
  mocks.scanCard.mockResolvedValue(stubResponse)
  mocks.stageScan.mockRejectedValue(new Error('IndexedDB unavailable'))
  mocks.pushHistory.mockImplementation(() => {
    throw new Error('QuotaExceededError: localStorage full')
  })
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
  render(<App />)
  pickFrontAndScan()
  // The paid-for result must survive a failed history write.
  await screen.findByText(/market value unavailable/i)
  expect(screen.queryByRole('alert')).toBeNull()
  expect(warn).toHaveBeenCalled()
  warn.mockRestore()
})

test('header nav is disabled while a scan is in flight', async () => {
  saveSettings({ provider: 'anthropic', apiKey: 'sk-test' })
  let finish!: (r: ScanResponse) => void
  mocks.scanCard.mockImplementation(() => new Promise(res => { finish = res }))
  render(<App />)
  pickFrontAndScan()
  await screen.findByRole('status')
  for (const name of ['Scan', 'Binder', 'History', 'Settings']) {
    expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(true)
  }
  finish(stubResponse)
  await screen.findByText(/market value unavailable/i)
  expect((screen.getByRole('button', { name: 'History' }) as HTMLButtonElement).disabled).toBe(false)
})

test('vibrates on scan success when the device supports it', async () => {
  saveSettings({ provider: 'anthropic', apiKey: 'sk-test' })
  const vibrate = vi.fn()
  Object.defineProperty(navigator, 'vibrate', { value: vibrate, configurable: true, writable: true })
  mocks.scanCard.mockResolvedValue(stubResponse)
  render(<App />)
  pickFrontAndScan()
  await screen.findByText(/market value unavailable/i)
  expect(vibrate).toHaveBeenCalledWith(50)
})

test('asking price flows into the verdict hero; no orphaned caption', async () => {
  saveSettings({ provider: 'anthropic', apiKey: 'sk-test' })
  const verdictResponse: ScanResponse = {
    ...stubResponse,
    verdict: { value_low: 50, value_high: 70, verdict: 'undervalued', reasoning: 'Cheap!' },
  }
  mocks.scanCard.mockResolvedValue(verdictResponse)
  render(<App />)
  fireEvent.change(screen.getByLabelText(/asking price/i), { target: { value: '20' } })
  pickFrontAndScan()
  // Delta proves askingPrice reached ResultsScreen: midpoint 60 − ask 20 = 40.
  await screen.findByText('≈ $40 below market')
  // The old standalone "Asking price: $N" caption is gone.
  expect(screen.queryByText(/^Asking price: \$/)).toBeNull()
})

test('reopening a scan from history restores its asking price', async () => {
  saveSettings({ provider: 'anthropic', apiKey: 'sk-test' })
  const verdictResponse: ScanResponse = {
    ...stubResponse,
    verdict: { value_low: 50, value_high: 70, verdict: 'undervalued', reasoning: 'Cheap!' },
  }
  mocks.scanCard.mockResolvedValue(verdictResponse)
  // History lists IndexedDB-staged scans; stub the listing with the staged
  // shape of this scan (staging itself is unit-tested in binderDb.test.ts).
  mocks.listStaged.mockResolvedValue([{
    record_id: 'r1',
    at: new Date().toISOString(),
    response: verdictResponse,
    askingPrice: 20,
    status: 'draft',
  }])
  render(<App />)
  fireEvent.change(screen.getByLabelText(/asking price/i), { target: { value: '20' } })
  pickFrontAndScan()
  await screen.findByText('≈ $40 below market')

  fireEvent.click(screen.getByRole('button', { name: 'History' }))
  // Rows load from IndexedDB asynchronously now.
  fireEvent.click(await screen.findByRole('button', { name: /unreadable photo/i }))
  expect(await screen.findByText('≈ $40 below market')).toBeDefined()
})

test('banner shows when mount health check fails; Retry re-checks and clears it', async () => {
  saveSettings({ provider: 'anthropic', apiKey: 'sk-test' })
  mocks.checkHealth.mockResolvedValueOnce(false)
  render(<App />)
  await screen.findByText(/scan server unreachable/i)
  // Server comes back; Retry re-runs the health check and clears the banner.
  mocks.checkHealth.mockResolvedValue(true)
  fireEvent.click(screen.getByRole('button', { name: /retry/i }))
  await waitForElementToBeRemoved(() => screen.queryByText(/scan server unreachable/i))
})

test('Retry keeps the banner up while the server is still down', async () => {
  saveSettings({ provider: 'anthropic', apiKey: 'sk-test' })
  mocks.checkHealth.mockResolvedValue(false)
  render(<App />)
  await screen.findByText(/scan server unreachable/i)
  fireEvent.click(screen.getByRole('button', { name: /retry/i }))
  await waitFor(() => expect(mocks.checkHealth).toHaveBeenCalledTimes(2))
  expect(screen.getByText(/scan server unreachable/i)).toBeDefined()
})

test('window online event re-checks health and clears the banner', async () => {
  saveSettings({ provider: 'anthropic', apiKey: 'sk-test' })
  mocks.checkHealth.mockResolvedValueOnce(false)
  render(<App />)
  await screen.findByText(/scan server unreachable/i)
  mocks.checkHealth.mockResolvedValue(true)
  fireEvent(window, new Event('online'))
  await waitForElementToBeRemoved(() => screen.queryByText(/scan server unreachable/i))
})

test('scan attempt while server down re-checks first and blocks when still down', async () => {
  saveSettings({ provider: 'anthropic', apiKey: 'sk-test' })
  mocks.checkHealth.mockResolvedValue(false)
  render(<App />)
  await screen.findByText(/scan server unreachable/i)
  pickFrontAndScan()
  // Recheck ran (mount + pre-scan) but the scan itself never fired.
  await waitFor(() => expect(mocks.checkHealth).toHaveBeenCalledTimes(2))
  expect(mocks.scanCard).not.toHaveBeenCalled()
  expect(screen.getByText(/scan server unreachable/i)).toBeDefined()
})

test('scan attempt while server down proceeds once health recovers', async () => {
  saveSettings({ provider: 'anthropic', apiKey: 'sk-test' })
  mocks.checkHealth.mockResolvedValueOnce(false)
  mocks.scanCard.mockResolvedValue(stubResponse)
  render(<App />)
  await screen.findByText(/scan server unreachable/i)
  // Server recovered by the time the user hits Scan.
  mocks.checkHealth.mockResolvedValue(true)
  pickFrontAndScan()
  await screen.findByText(/market value unavailable/i)
  expect(mocks.scanCard).toHaveBeenCalledOnce()
  expect(screen.queryByText(/scan server unreachable/i)).toBeNull()
})

test('failed scan shows dismissible error and stays on scan view', async () => {
  saveSettings({ provider: 'anthropic', apiKey: 'sk-test' })
  mocks.scanCard.mockRejectedValue(new ApiError('Scan failed (500). Try again.'))
  render(<App />)
  pickFrontAndScan()
  const alert = await screen.findByRole('alert')
  expect(alert.textContent).toContain('Scan failed (500). Try again.')
  // Still on the scan view.
  expect(screen.getByRole('button', { name: /scan card/i })).toBeDefined()
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
  expect(screen.queryByRole('alert')).toBeNull()
})
