// API calls for publishing to and reading from The Binder (style of api.ts).

import { ApiError } from './api'
import { authHeaders } from './auth/authApi'
import { loadSession } from './auth/session'
import type { AuthUser } from './auth/session'
import { clientId, getImages, listStaged, setStatus } from './binderDb'
import type {
  BinderCard,
  CardRecordDraft,
  HiveStatus,
  PublishJobStatus,
  StagedCard,
} from './binderTypes'

const API_BASE = import.meta.env.VITE_API_URL ?? ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response
  try {
    resp = await fetch(`${API_BASE}${path}`, init)
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw new ApiError('Could not reach the server. Check your connection.')
  }
  if (!resp.ok) {
    const detail = await resp.json().then(b => b.detail).catch(() => null)
    throw new ApiError(detail ?? `Request failed (${resp.status}). Try again.`)
  }
  return resp.json()
}

// Build the publishable record from a staged scan. The server completes it
// with the uploaded image URLs. `user` (default: the current session) fills
// the attribution block; the server re-stamps it from the Bearer token, so
// this is for the client's own bookkeeping, never a trusted identity claim.
export function toDraft(staged: StagedCard,
                        user: AuthUser | null = loadSession()?.user ?? null): CardRecordDraft {
  const { vision, comps, verdict } = staged.response
  if (!vision.identity) {
    throw new ApiError('This scan could not read the card, so it cannot be published.')
  }
  return {
    v: 1,
    kind: 'card',
    record_id: staged.record_id,
    identity: vision.identity,
    condition: vision.condition,
    slab: vision.slab ?? null,
    authenticity: vision.authenticity,
    verdict,
    comps: comps ? { summary: comps, top_sales: [], as_of: staged.at } : null,
    asking_price: staged.askingPrice ?? null,
    attribution: {
      client_id: clientId(),
      display_name: user?.display_name ?? null,
      user_id: user?.id ?? null,
      hive_display_key: user?.hive_display_key ?? null,
    },
    scanned_at: staged.at,
  }
}

export async function publishCard(
  staged: StagedCard,
  front: Blob,
  back: Blob | null,
): Promise<PublishJobStatus> {
  const form = new FormData()
  form.append('record', JSON.stringify(toDraft(staged)))
  form.append('front', front, 'front.jpg')
  if (back) form.append('back', back, 'back.jpg')
  // Signed in: the token attributes the post to the user. Signed out: the
  // request is byte-for-byte what it was before sign-in existed.
  return request<PublishJobStatus>('/api/publish',
                                   { method: 'POST', body: form, headers: authHeaders() })
}

export function getPublishStatus(jobId: string): Promise<PublishJobStatus> {
  return request(`/api/publish/${encodeURIComponent(jobId)}`)
}

// `owner` = a hive_display_key: the same community feed, narrowed server-side
// to one collector ("My Collection").
export function listBinder(cursor?: {
  start_author: string
  start_permlink: string
}, owner?: string): Promise<{ cards: BinderCard[]; next: { start_author: string; start_permlink: string } | null }> {
  const params = new URLSearchParams({ limit: '20' })
  if (cursor) {
    params.set('start_author', cursor.start_author)
    params.set('start_permlink', cursor.start_permlink)
  }
  if (owner) params.set('owner', owner)
  return request(`/api/cards?${params}`)
}

export function getBinderCard(permlink: string): Promise<BinderCard> {
  return request(`/api/cards/${encodeURIComponent(permlink)}`)
}

export function refreshComps(permlink: string): Promise<{ job_id: string }> {
  return request(`/api/cards/${encodeURIComponent(permlink)}/refresh-comps`,
                 { method: 'POST' })
}

export async function hiveStatus(): Promise<HiveStatus> {
  try {
    return await request<HiveStatus>('/api/hive/status')
  } catch {
    return { configured: false }
  }
}

// Re-submit consented publishes that never reached the server (offline at
// tap time). Two safety rules:
// - only cards owned by the CURRENT user are retried — a second account on
//   this device must never trigger another user's publishes;
// - the error message from a budget pause (429) is surfaced on the card,
//   but consent stays so the next sweep retries after the window resets.
export async function resumePendingPublishes(): Promise<void> {
  const staged = await listStaged()
  const current = loadSession()?.user
  for (const card of staged) {
    if (card.status !== 'draft' || !card.publishRequested || card.legacy) continue
    if (current && card.user_id && card.user_id !== current.id) continue
    const images = await getImages(card.record_id)
    if (!images) continue
    try {
      const job = await publishCard(card, images.front, images.back)
      await setStatus(card.record_id, {
        status: 'queued', job_id: job.job_id, permlink: job.permlink,
        error: undefined,
      })
    } catch (err) {
      // Still offline (or server down) — stays publishRequested for next sweep.
      if (err instanceof ApiError && err.message.includes('hourly publish limit')) {
        // Don't overwrite a useful error on every 30s poll.
        if (card.error !== err.message) {
          await setStatus(card.record_id, { error: err.message }).catch(() => {})
        }
      }
    }
  }
}
