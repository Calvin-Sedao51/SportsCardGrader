import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, expect, test, vi } from 'vitest'
import { signOut, useAuth } from './useAuth'
import { loadSession, saveSession } from './session'

const mocks = vi.hoisted(() => ({
  googleSignIn: vi.fn(),
  isGoogleSignInConfigured: vi.fn(),
  loginWithGoogle: vi.fn(),
  provisionHive: vi.fn(),
}))

vi.mock('./googleSignIn', () => ({
  googleSignIn: mocks.googleSignIn,
  isGoogleSignInConfigured: mocks.isGoogleSignInConfigured,
}))
vi.mock('./authApi', async importOriginal => ({
  ...(await importOriginal<typeof import('./authApi')>()),
  loginWithGoogle: mocks.loginWithGoogle,
  provisionHive: mocks.provisionHive,
}))

const USER = { id: 'u1', email: 'calvin@example.com', display_name: 'Calvin',
               hive_display_key: 'binder-abcdef12', created_at: '2026-09-16T00:00:00Z' }
const PRESENCE = { user_id: 'u1', display_name: 'Calvin', hive_display_key: 'binder-abcdef12',
                   hive: { mode: 'shared', posting_account: 'thebinder', display_key: 'binder-abcdef12' } }

beforeEach(() => {
  sessionStorage.clear()
  signOut()
  mocks.googleSignIn.mockReset()
  mocks.isGoogleSignInConfigured.mockReset().mockReturnValue(true)
  mocks.loginWithGoogle.mockReset().mockResolvedValue({ token: 'app.jwt', user: USER, created: true })
  mocks.provisionHive.mockReset().mockResolvedValue(PRESENCE)
})

test('starts signed out, reporting whether sign-in is configured', () => {
  const { result } = renderHook(() => useAuth())
  expect(result.current.status).toBe('signed_out')
  expect(result.current.user).toBeNull()
  expect(result.current.configured).toBe(true)
})

test('signIn: Google credential -> app token -> Hive presence -> signed in', async () => {
  mocks.googleSignIn.mockImplementation(async (cb: (c: string) => void) => { cb('google.id.token'); return true })
  const { result } = renderHook(() => useAuth())
  await act(() => result.current.signIn())
  await waitFor(() => expect(result.current.status).toBe('signed_in'))
  expect(mocks.loginWithGoogle).toHaveBeenCalledWith('google.id.token')
  expect(mocks.provisionHive).toHaveBeenCalledOnce()
  expect(result.current.user).toEqual(USER)
  expect(loadSession()?.token).toBe('app.jwt') // persisted for binderApi's auth headers
})

test('signIn when the provider is not configured leaves the user signed out with a message', async () => {
  mocks.isGoogleSignInConfigured.mockReturnValue(false)
  mocks.googleSignIn.mockResolvedValue(false)
  const { result } = renderHook(() => useAuth())
  await act(() => result.current.signIn())
  expect(result.current.status).toBe('signed_out')
  expect(result.current.error).toMatch(/not (configured|available)/i)
  expect(mocks.loginWithGoogle).not.toHaveBeenCalled()
})

test('a rejected credential surfaces the server message and clears the session', async () => {
  const { ApiError } = await import('../api')
  mocks.googleSignIn.mockImplementation(async (cb: (c: string) => void) => { cb('bad'); return true })
  mocks.loginWithGoogle.mockRejectedValue(new ApiError('Google sign-in was rejected: bad aud'))
  const { result } = renderHook(() => useAuth())
  await act(() => result.current.signIn())
  await waitFor(() => expect(result.current.error).toMatch(/bad aud/))
  expect(result.current.status).toBe('signed_out')
  expect(loadSession()).toBeNull()
})

test('an existing session is restored on load and signOut drops it', async () => {
  saveSession({ token: 'app.jwt', user: USER })
  const { result } = renderHook(() => useAuth())
  expect(result.current.status).toBe('signed_in')
  expect(result.current.user?.hive_display_key).toBe('binder-abcdef12')
  act(() => result.current.signOut())
  expect(result.current.status).toBe('signed_out')
  expect(loadSession()).toBeNull()
})
