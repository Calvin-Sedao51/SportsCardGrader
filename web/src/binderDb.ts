// IndexedDB staging for the Binder publish flow. Scans (with their prepared
// image blobs) accumulate here until the user publishes them to Hive.
// Settings stay in localStorage (storage.ts); image blobs don't fit there.

import { openDB, type DBSchema, type IDBPDatabase } from 'idb'
import type { StagedCard } from './binderTypes'
import { loadHistory } from './storage'
import type { Identity, ScanResponse } from './types'

export const STAGED_LIMIT = 500
const DB_NAME = 'cardscanner'
const CLIENT_ID_KEY = 'cardscanner.client_id'
const MIGRATED_KEY = 'cardscanner.history_migrated'

// Images are stored as ArrayBuffer + mime type, not Blob: Blob-in-IndexedDB
// is unreliable across environments (and historically broken in Safari).
interface ImageRow {
  record_id: string
  side: 'front' | 'back'
  data: ArrayBuffer
  type: string
}

interface BinderSchema extends DBSchema {
  cards: { key: string; value: StagedCard }
  images: { key: [string, string]; value: ImageRow }
}

let dbPromise: Promise<IDBPDatabase<BinderSchema>> | null = null

function db(): Promise<IDBPDatabase<BinderSchema>> {
  dbPromise ??= openDB<BinderSchema>(DB_NAME, 1, {
    upgrade(database) {
      database.createObjectStore('cards', { keyPath: 'record_id' })
      database.createObjectStore('images', { keyPath: ['record_id', 'side'] })
    },
  })
  return dbPromise
}

export async function _resetDbForTests(): Promise<void> {
  if (dbPromise) (await dbPromise).close()
  dbPromise = null
  await new Promise<void>(resolve => {
    const req = indexedDB.deleteDatabase(DB_NAME)
    req.onsuccess = req.onerror = req.onblocked = () => resolve()
  })
}

export function clientId(): string {
  let id = localStorage.getItem(CLIENT_ID_KEY)
  if (!id) {
    id = crypto.randomUUID()
    localStorage.setItem(CLIENT_ID_KEY, id)
  }
  return id
}

export async function stageScan(
  response: ScanResponse,
  askingPrice: number | null,
  front: Blob,
  back: Blob | null,
): Promise<StagedCard> {
  const card: StagedCard = {
    record_id: crypto.randomUUID(),
    at: new Date().toISOString(),
    response,
    askingPrice: askingPrice ?? undefined,
    status: 'draft',
  }
  const rows: ImageRow[] = [
    { record_id: card.record_id, side: 'front', data: await front.arrayBuffer(), type: front.type },
  ]
  if (back) {
    rows.push({ record_id: card.record_id, side: 'back', data: await back.arrayBuffer(), type: back.type })
  }
  const database = await db()
  const tx = database.transaction(['cards', 'images'], 'readwrite')
  await tx.objectStore('cards').put(card)
  for (const row of rows) await tx.objectStore('images').put(row)
  await tx.done
  await pruneStaged(STAGED_LIMIT)
  return card
}

// Entries staged before the Identity.player -> subject rename. Migrated in
// memory at the read boundary so every render path sees `subject`.
type LegacyIdentity = Omit<Identity, 'subject'> & { subject?: string; player?: string }

function migrateLegacyIdentity(card: StagedCard): StagedCard {
  const identity = card.response.vision.identity as LegacyIdentity | null
  if (!identity || identity.subject != null || identity.player == null) return card
  const { player, ...rest } = identity
  return {
    ...card,
    response: {
      ...card.response,
      vision: { ...card.response.vision, identity: { ...rest, subject: player } },
    },
  }
}

export async function listStaged(): Promise<StagedCard[]> {
  const all = await (await db()).getAll('cards')
  return all.map(migrateLegacyIdentity).sort((a, b) => b.at.localeCompare(a.at))
}

export async function getImages(
  recordId: string,
): Promise<{ front: Blob; back: Blob | null } | null> {
  const database = await db()
  const front = await database.get('images', [recordId, 'front'])
  if (!front) return null
  const back = await database.get('images', [recordId, 'back'])
  return {
    front: new Blob([front.data], { type: front.type }),
    back: back ? new Blob([back.data], { type: back.type }) : null,
  }
}

export async function setStatus(
  recordId: string,
  patch: Partial<StagedCard>,
): Promise<void> {
  const database = await db()
  const card = await database.get('cards', recordId)
  if (!card) return
  await database.put('cards', { ...card, ...patch, record_id: recordId })
}

export async function deleteStaged(recordId: string): Promise<void> {
  const database = await db()
  const tx = database.transaction(['cards', 'images'], 'readwrite')
  await tx.objectStore('cards').delete(recordId)
  await tx.objectStore('images').delete([recordId, 'front'])
  await tx.objectStore('images').delete([recordId, 'back'])
  await tx.done
}

// Cap storage by dropping the OLDEST drafts only — anything queued, published,
// or failed represents user intent or an on-chain record and is never pruned.
export async function pruneStaged(limit: number): Promise<void> {
  const staged = await listStaged() // newest first
  const over = staged.length - limit
  if (over <= 0) return
  const prunable = staged.filter(s => s.status === 'draft' && !s.publishRequested)
  for (const victim of prunable.slice(-over).reverse()) {
    await deleteStaged(victim.record_id)
  }
}

// One-time import of the old localStorage history. Legacy entries have no
// image blobs (history never saved them), so they are viewable but can never
// be published — flagged `legacy` so the UI can say why.
export async function migrateFromLocalStorage(): Promise<number> {
  if (localStorage.getItem(MIGRATED_KEY)) return 0
  const history = loadHistory()
  const database = await db()
  let imported = 0
  for (const entry of history) {
    const card: StagedCard = {
      record_id: crypto.randomUUID(),
      at: entry.at,
      response: entry.response,
      askingPrice: entry.askingPrice,
      status: 'draft',
      legacy: true,
    }
    await database.put('cards', card)
    imported++
  }
  localStorage.setItem(MIGRATED_KEY, '1')
  return imported
}
