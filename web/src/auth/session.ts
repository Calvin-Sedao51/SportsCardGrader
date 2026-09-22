// The signed-in session: the app JWT + the user it belongs to.
//
// Storage choice (deliberate): memory + sessionStorage, NEVER localStorage.
// localStorage already holds the user's AI provider key (storage.ts). Keeping
// the identity token out of that same bucket means a leak of one store — an
// XSS payload dumping localStorage, a shared-device "remember" habit, a
// backup/sync of the origin's local data — cannot hand an attacker both the
// billing credential and the identity in one grab. sessionStorage is
// per-tab and dies with it; the app token is 30-day and re-obtainable with
// one tap, so the trade-off is a re-sign-in per browser session, not lost data.

export interface AuthUser {
  id: string
  email: string | null
  display_name: string
  hive_display_key: string // 'binder-<8 hex>' — the public collector handle
  created_at: string
}

export interface AuthSession {
  token: string
  user: AuthUser
}

const SESSION_KEY = 'cardscanner.auth'

let memory: AuthSession | null = null

export function loadSession(): AuthSession | null {
  if (memory) return memory
  try {
    const raw = sessionStorage.getItem(SESSION_KEY)
    const parsed = raw ? (JSON.parse(raw) as AuthSession) : null
    if (parsed && typeof parsed.token === 'string' && parsed.user?.hive_display_key) {
      memory = parsed
    }
  } catch {
    memory = null
  }
  return memory
}

export function saveSession(session: AuthSession): void {
  memory = session
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(session))
  } catch {
    // Private mode / quota: the in-memory copy still carries this page load.
  }
}

export function clearSession(): void {
  memory = null
  try {
    sessionStorage.removeItem(SESSION_KEY)
  } catch {
    // nothing to clear
  }
}

export function getToken(): string | null {
  return loadSession()?.token ?? null
}
