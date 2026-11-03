// googleSignIn(callback): ask Google for an ID token and hand it to callback.
// Resolves with what happened to the prompt:
//   'shown'       the One Tap / FedCM prompt opened (or a credential arrived);
//   'not_shown'   Google suppressed it (cooldown, third-party-cookie or FedCM
//                 block, Safari, dismissed) or never reported back in time;
//   'unavailable' sign-in is not configured on this build.
// The caller shows a message for the last two; it must never wait on 'shown'.
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

// GIS PromptMomentNotification, typed locally (no @types package for the
// runtime-loaded script). prompt() may call the listener several times: a
// display moment, then a skipped or dismissed moment.
interface PromptMomentNotification {
  isDisplayed(): boolean
  isNotDisplayed(): boolean
  getNotDisplayedReason(): string
  isSkippedMoment(): boolean
  getSkippedReason(): string
  isDismissedMoment(): boolean
  getDismissedReason(): string
}

interface GisIdApi {
  initialize(config: { client_id: string; callback: (r: { credential: string }) => void;
                       auto_select?: boolean }): void
  prompt(listener?: (notification: PromptMomentNotification) => void): void
}

type GisWindow = Window & { google?: { accounts?: { id?: GisIdApi } } }

export type CredentialCallback = (credential: string) => void

export type GoogleSignInOutcome = 'shown' | 'not_shown' | 'unavailable'

// If GIS reports nothing at all (seen with FedCM-mode browsers), give up on
// the prompt after this long so the UI never sticks on "Signing in…".
export const PROMPT_TIMEOUT_MS = 60_000

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

// 'shown' once the prompt is on screen or a credential arrives (FedCM may
// deliver one without a display moment); 'not_shown' when Google declines to
// show it, the user dismisses it, or nothing is reported before the timeout.
function promptForCredential(gis: GisIdApi, callback: CredentialCallback): Promise<GoogleSignInOutcome> {
  return new Promise(resolve => {
    let timer: ReturnType<typeof setTimeout> | undefined
    const settle = (outcome: GoogleSignInOutcome) => { clearTimeout(timer); resolve(outcome) }
    timer = setTimeout(() => settle('not_shown'), PROMPT_TIMEOUT_MS)
    gis.initialize({
      client_id: clientId(),
      callback: r => { settle('shown'); callback(r.credential) },
    })
    gis.prompt(n => {
      if (n.isDisplayed()) settle('shown')
      else if (n.isNotDisplayed() || n.isSkippedMoment()) settle('not_shown')
      else if (n.isDismissedMoment()) {
        settle(n.getDismissedReason() === 'credential_returned' ? 'shown' : 'not_shown')
      }
    })
  })
}

export async function googleSignIn(callback: CredentialCallback): Promise<GoogleSignInOutcome> {
  if (!isGoogleSignInConfigured()) return (await stubSignIn(callback)) ? 'shown' : 'unavailable'
  const gis = await loadGis()
  if (!gis) return 'unavailable'
  return promptForCredential(gis, callback)
}
