import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { googleSignIn, isGoogleSignInConfigured } from './googleSignIn'

type Win = Window & { google?: unknown }

type MomentFlag = 'isDisplayed' | 'isNotDisplayed' | 'isSkippedMoment' | 'isDismissedMoment'

// A GIS PromptMomentNotification with exactly one flag raised.
function moment(flag: MomentFlag, reason = '') {
  return {
    isDisplayed: () => flag === 'isDisplayed',
    isNotDisplayed: () => flag === 'isNotDisplayed',
    getNotDisplayedReason: () => reason,
    isSkippedMoment: () => flag === 'isSkippedMoment',
    getSkippedReason: () => reason,
    isDismissedMoment: () => flag === 'isDismissedMoment',
    getDismissedReason: () => reason,
  }
}

type Notify = (n: ReturnType<typeof moment>) => void

function installGis(prompt: (notify: Notify) => void) {
  const initialize = vi.fn()
  ;(window as Win).google = { accounts: { id: { initialize, prompt: vi.fn(prompt) } } }
  return initialize
}

beforeEach(() => {
  vi.unstubAllEnvs()
  delete (window as Win).google
  document.querySelectorAll('script[data-gsi]').forEach(s => s.remove())
})
afterEach(() => vi.restoreAllMocks())

test('without a client id the stub is a documented no-op that reports unavailable', async () => {
  vi.stubEnv('VITE_GOOGLE_CLIENT_ID', '')
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
  const callback = vi.fn()
  expect(isGoogleSignInConfigured()).toBe(false)
  expect(await googleSignIn(callback)).toBe('unavailable')
  expect(callback).not.toHaveBeenCalled()
  expect(warn).toHaveBeenCalledOnce()
})

test('with a client id it initialises Google Identity Services and hands back the credential', async () => {
  vi.stubEnv('VITE_GOOGLE_CLIENT_ID', '123.apps.googleusercontent.com')
  const initialize = installGis(notify => notify(moment('isDisplayed')))
  const callback = vi.fn()
  expect(isGoogleSignInConfigured()).toBe(true)
  expect(await googleSignIn(callback)).toBe('shown')
  expect(initialize).toHaveBeenCalledWith(expect.objectContaining({
    client_id: '123.apps.googleusercontent.com' }))
  // GIS calls back with { credential }; we forward just the ID token.
  initialize.mock.calls[0][0].callback({ credential: 'google.id.token' })
  expect(callback).toHaveBeenCalledWith('google.id.token')
})

test('with a client id but no GIS script yet, it injects the script tag once', async () => {
  vi.stubEnv('VITE_GOOGLE_CLIENT_ID', '123.apps.googleusercontent.com')
  const first = googleSignIn(() => {})
  const second = googleSignIn(() => {})
  const tags = document.querySelectorAll('script[data-gsi]')
  expect(tags.length).toBe(1)
  expect((tags[0] as HTMLScriptElement).src).toBe('https://accounts.google.com/gsi/client')
  // Simulate the script arriving with the global attached.
  installGis(notify => notify(moment('isDisplayed')))
  tags[0].dispatchEvent(new Event('load'))
  expect(await first).toBe('shown')
  expect(await second).toBe('shown')
})

test('script load failure resolves false instead of hanging', async () => {
  vi.stubEnv('VITE_GOOGLE_CLIENT_ID', '123.apps.googleusercontent.com')
  const pending = googleSignIn(() => {})
  document.querySelector('script[data-gsi]')!.dispatchEvent(new Event('error'))
  expect(await pending).toBe('unavailable')
})

// -- One Tap suppressed: cooldown, FedCM / third-party-cookie block, Safari --

test('a not-displayed moment resolves not_shown instead of hanging', async () => {
  vi.stubEnv('VITE_GOOGLE_CLIENT_ID', '123.apps.googleusercontent.com')
  installGis(notify => notify(moment('isNotDisplayed', 'opt_out_or_no_session')))
  expect(await googleSignIn(() => {})).toBe('not_shown')
})

test('a skipped moment resolves not_shown', async () => {
  vi.stubEnv('VITE_GOOGLE_CLIENT_ID', '123.apps.googleusercontent.com')
  installGis(notify => notify(moment('isSkippedMoment', 'user_cancel')))
  expect(await googleSignIn(() => {})).toBe('not_shown')
})

test('a dismissed moment without a credential resolves not_shown', async () => {
  vi.stubEnv('VITE_GOOGLE_CLIENT_ID', '123.apps.googleusercontent.com')
  installGis(notify => notify(moment('isDismissedMoment', 'cancel_called')))
  expect(await googleSignIn(() => {})).toBe('not_shown')
})

test('a dismissed moment because the credential was returned counts as shown', async () => {
  vi.stubEnv('VITE_GOOGLE_CLIENT_ID', '123.apps.googleusercontent.com')
  installGis(notify => notify(moment('isDismissedMoment', 'credential_returned')))
  expect(await googleSignIn(() => {})).toBe('shown')
})

test('a credential arriving before any moment notification counts as shown', async () => {
  vi.stubEnv('VITE_GOOGLE_CLIENT_ID', '123.apps.googleusercontent.com')
  const initialize = installGis(() => {})
  const callback = vi.fn()
  const pending = googleSignIn(callback)
  await vi.waitFor(() => expect(initialize).toHaveBeenCalledOnce())
  initialize.mock.calls[0][0].callback({ credential: 'google.id.token' })
  expect(await pending).toBe('shown')
  expect(callback).toHaveBeenCalledWith('google.id.token')
})

test('no notification at all resolves not_shown after the hard timeout', async () => {
  vi.useFakeTimers()
  try {
    vi.stubEnv('VITE_GOOGLE_CLIENT_ID', '123.apps.googleusercontent.com')
    installGis(() => {})
    const pending = googleSignIn(() => {})
    await vi.advanceTimersByTimeAsync(59_000)
    const settled = vi.fn()
    pending.then(settled)
    await vi.advanceTimersByTimeAsync(0)
    expect(settled).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1_000)
    expect(await pending).toBe('not_shown')
  } finally {
    vi.useRealTimers()
  }
})
