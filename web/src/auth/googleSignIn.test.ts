import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { googleSignIn, isGoogleSignInConfigured } from './googleSignIn'

type Win = Window & { google?: unknown }

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
  expect(await googleSignIn(callback)).toBe(false)
  expect(callback).not.toHaveBeenCalled()
  expect(warn).toHaveBeenCalledOnce()
})

test('with a client id it initialises Google Identity Services and hands back the credential', async () => {
  vi.stubEnv('VITE_GOOGLE_CLIENT_ID', '123.apps.googleusercontent.com')
  const initialize = vi.fn()
  const prompt = vi.fn()
  ;(window as Win).google = { accounts: { id: { initialize, prompt } } }
  const callback = vi.fn()
  expect(isGoogleSignInConfigured()).toBe(true)
  expect(await googleSignIn(callback)).toBe(true)
  expect(initialize).toHaveBeenCalledWith(expect.objectContaining({
    client_id: '123.apps.googleusercontent.com' }))
  expect(prompt).toHaveBeenCalledOnce()
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
  ;(window as Win).google = { accounts: { id: { initialize: vi.fn(), prompt: vi.fn() } } }
  tags[0].dispatchEvent(new Event('load'))
  expect(await first).toBe(true)
  expect(await second).toBe(true)
})

test('script load failure resolves false instead of hanging', async () => {
  vi.stubEnv('VITE_GOOGLE_CLIENT_ID', '123.apps.googleusercontent.com')
  const pending = googleSignIn(() => {})
  document.querySelector('script[data-gsi]')!.dispatchEvent(new Event('error'))
  expect(await pending).toBe(false)
})
