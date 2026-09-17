// Sign-in API calls (style of binderApi.ts).

import { ApiError } from '../api'
import { getToken } from './session'
import type { AuthUser } from './session'

const API_BASE = import.meta.env.VITE_API_URL ?? ''

export interface LoginResult {
  token: string
  user: AuthUser
  created: boolean
}

export interface HivePresence {
  user_id: string
  display_name: string
  hive_display_key: string
  hive: { mode: 'shared' | 'per_user'; posting_account: string; display_key: string }
}

// Authorization header for endpoints that attribute work to the signed-in
// user; empty when signed out so the anonymous path sends exactly what it
// always did.
export function authHeaders(): Record<string, string> {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response
  try {
    resp = await fetch(`${API_BASE}${path}`, init)
  } catch {
    throw new ApiError('Could not reach the server. Check your connection.')
  }
  if (!resp.ok) {
    const detail = await resp.json().then(b => b.detail).catch(() => null)
    throw new ApiError(detail ?? `Request failed (${resp.status}). Try again.`)
  }
  return resp.json()
}

export function loginWithGoogle(credential: string): Promise<LoginResult> {
  return request('/api/auth/google', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ credential }),
  })
}

// Idempotent: called after every login so first-login provisioning and
// "restore my presence" are the same call.
export function provisionHive(): Promise<HivePresence> {
  return request('/api/auth/hive', { method: 'POST', headers: authHeaders() })
}
