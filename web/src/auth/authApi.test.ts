import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ApiError } from '../api'
import { authHeaders, loginWithGoogle, provisionHive } from './authApi'
import { clearSession, saveSession } from './session'

const USER = { id: 'u1', email: 'calvin@example.com', display_name: 'Calvin',
               hive_display_key: 'binder-abcdef12', created_at: '2026-09-16T00:00:00Z' }

beforeEach(() => { sessionStorage.clear(); clearSession() })
afterEach(() => vi.restoreAllMocks())

function mockFetch(body: unknown, status = 200) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(() =>
    Promise.resolve(new Response(JSON.stringify(body), { status })))
}

test('loginWithGoogle posts the credential and returns token + user', async () => {
  const mock = mockFetch({ token: 'app.jwt', user: USER, created: true })
  const result = await loginWithGoogle('google.id.token')
  expect(result).toEqual({ token: 'app.jwt', user: USER, created: true })
  const [url, init] = mock.mock.calls[0]
  expect(String(url)).toContain('/api/auth/google')
  expect(init!.method).toBe('POST')
  expect(JSON.parse(init!.body as string)).toEqual({ credential: 'google.id.token' })
})

test('login failure surfaces the server detail as an ApiError', async () => {
  mockFetch({ detail: 'Google sign-in was rejected: token was issued for a different app' }, 401)
  await expect(loginWithGoogle('bad')).rejects.toThrow(ApiError)
  await expect(loginWithGoogle('bad')).rejects.toThrow(/different app/)
})

test('provisionHive sends the bearer token and returns the presence', async () => {
  saveSession({ token: 'app.jwt', user: USER })
  const presence = { user_id: 'u1', display_name: 'Calvin', hive_display_key: 'binder-abcdef12',
                     hive: { mode: 'shared', posting_account: 'thebinder', display_key: 'binder-abcdef12' } }
  const mock = mockFetch(presence)
  expect(await provisionHive()).toEqual(presence)
  const [url, init] = mock.mock.calls[0]
  expect(String(url)).toContain('/api/auth/hive')
  expect((init!.headers as Record<string, string>).Authorization).toBe('Bearer app.jwt')
})

test('authHeaders is empty when signed out and carries the token when signed in', () => {
  expect(authHeaders()).toEqual({})
  saveSession({ token: 'app.jwt', user: USER })
  expect(authHeaders()).toEqual({ Authorization: 'Bearer app.jwt' })
})
