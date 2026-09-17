// googleSignIn(callback): ask Google for an ID token and hand it to callback.
// Resolves true when a sign-in prompt was shown, false when sign-in is not
// available on this build (the caller shows a "not configured" message).
//
// Provider selection:
//   * VITE_GOOGLE_CLIENT_ID set -> Google Identity Services (GIS), loaded at
//     runtime from accounts.google.com — no npm package, no Firebase project
//     required. The credential is a Google ID token the server verifies.
//   * otherwise -> firebaseStub, the documented no-op dev fallback.
// A real Firebase integration would slot in here as a third branch behind
// the same interface (see firebaseStub.ts).

import { googleSignIn as stubSignIn } from './firebaseStub'

const GSI_SRC = 'https://accounts.google.com/gsi/client'

interface GisIdApi {
  initialize(config: { client_id: string; callback: (r: { credential: string }) => void;
                       auto_select?: boolean }): void
  prompt(): void
}

type GisWindow = Window & { google?: { accounts?: { id?: GisIdApi } } }

export type CredentialCallback = (credential: string) => void

export function clientId(): string {
  return (import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined) ?? ''
}

export function isGoogleSignInConfigured(): boolean {
  return clientId() !== ''
}

// Only the in-flight load is cached; once the script has run, the global
// (window.google) is the source of truth.
let scriptLoading: Promise<GisIdApi | null> | null = null

function loadGis(): Promise<GisIdApi | null> {
  const existing = (window as GisWindow).google?.accounts?.id
  if (existing) return Promise.resolve(existing)
  scriptLoading ??= new Promise(resolve => {
    const tag = document.createElement('script')
    tag.src = GSI_SRC
    tag.async = true
    tag.dataset.gsi = '1'
    const settle = (api: GisIdApi | null) => { scriptLoading = null; resolve(api) }
    tag.onload = () => settle((window as GisWindow).google?.accounts?.id ?? null)
    tag.onerror = () => settle(null)
    document.head.appendChild(tag)
  })
  return scriptLoading
}

export async function googleSignIn(callback: CredentialCallback): Promise<boolean> {
  if (!isGoogleSignInConfigured()) return stubSignIn(callback)
  const gis = await loadGis()
  if (!gis) return false
  gis.initialize({ client_id: clientId(), callback: r => callback(r.credential) })
  gis.prompt()
  return true
}
