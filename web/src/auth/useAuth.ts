// Sign-in state shared by every screen (one module-level store, read with
// useSyncExternalStore). Flow: Google credential -> POST /api/auth/google
// (app JWT) -> POST /api/auth/hive (auto-provision the Hive presence).

import { useSyncExternalStore } from 'react'
import { ApiError } from '../api'
import { loginWithGoogle, provisionHive } from './authApi'
import { googleSignIn, isGoogleSignInConfigured } from './googleSignIn'
import { clearSession, loadSession, saveSession } from './session'
import type { AuthUser } from './session'

export type AuthStatus = 'signed_out' | 'signing_in' | 'signed_in'

export interface AuthState {
  status: AuthStatus
  user: AuthUser | null
  error: string | null
}

export const NOT_CONFIGURED_MESSAGE =
  'Sign-in is not configured on this build. Scanning and the community feed still work.'

function initial(): AuthState {
  const session = loadSession()
  return session
    ? { status: 'signed_in', user: session.user, error: null }
    : { status: 'signed_out', user: null, error: null }
}

let state: AuthState = initial()
const listeners = new Set<() => void>()

function set(next: AuthState) {
  state = next
  listeners.forEach(l => l())
}

// Pick up a session written to sessionStorage after this module loaded
// (runs from an effect, so notifying here never happens mid-render).
function rehydrate() {
  if (state.status !== 'signed_out') return
  const session = loadSession()
  if (session) set({ status: 'signed_in', user: session.user, error: null })
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  rehydrate()
  return () => { listeners.delete(listener) }
}

async function completeLogin(credential: string): Promise<void> {
  try {
    const { token, user } = await loginWithGoogle(credential)
    saveSession({ token, user })
    set({ status: 'signed_in', user, error: null })
    // First login provisions the Hive presence; later logins just confirm it.
    const presence = await provisionHive()
    const refreshed = { ...user, display_name: presence.display_name,
                        hive_display_key: presence.hive_display_key }
    saveSession({ token, user: refreshed })
    set({ status: 'signed_in', user: refreshed, error: null })
  } catch (err) {
    clearSession()
    set({ status: 'signed_out', user: null,
          error: err instanceof ApiError ? err.message : 'Sign-in failed. Try again.' })
  }
}

export async function signIn(): Promise<void> {
  set({ ...state, status: 'signing_in', error: null })
  const shown = await googleSignIn(credential => { completeLogin(credential) })
  if (!shown) {
    set({ status: 'signed_out', user: null, error: NOT_CONFIGURED_MESSAGE })
  }
}

export function signOut(): void {
  clearSession()
  set({ status: 'signed_out', user: null, error: null })
}

export function useAuth() {
  const snapshot = useSyncExternalStore(subscribe, () => state, () => state)
  return { ...snapshot, configured: isGoogleSignInConfigured(), signIn, signOut }
}
