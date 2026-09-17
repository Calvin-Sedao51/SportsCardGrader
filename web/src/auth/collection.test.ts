import { expect, test } from 'vitest'
import { filterMine, isMine } from './collection'
import type { BinderCard, CardRecord } from '../binderTypes'

function card(id: string, key: string | null): CardRecord {
  return {
    v: 1, kind: 'card', record_id: id,
    identity: { subject: 'Luka Doncic', year: '2018', set_name: 'Panini Prizm', card_number: null,
                variant: null, search_string: 'x', confidence: 0.9 },
    condition: null, slab: null, authenticity: null, verdict: null, comps: null,
    images: { front: 'https://images.hive.blog/DQm/front.jpg', back: null },
    asking_price: null,
    attribution: { client_id: 'c1', display_name: null, user_id: key ? 'u' : null,
                   hive_display_key: key },
    scanned_at: '2026-09-16T00:00:00Z',
  }
}

function entry(id: string, key: string | null, topLevel = true): BinderCard {
  const c = card(id, key)
  return { permlink: `card-${id}`, author: 'thebinder', created: null, card: c,
           ...(topLevel ? { attribution: c.attribution } : {}) }
}

test('isMine matches on the feed entry attribution block', () => {
  expect(isMine(entry('a', 'binder-11111111'), 'binder-11111111')).toBe(true)
  expect(isMine(entry('a', 'binder-22222222'), 'binder-11111111')).toBe(false)
  expect(isMine(entry('a', null), 'binder-11111111')).toBe(false)
})

test('isMine falls back to the embedded card attribution for older entries', () => {
  expect(isMine(entry('a', 'binder-11111111', false), 'binder-11111111')).toBe(true)
})

test('filterMine keeps only my cards, in feed order', () => {
  const feed = [entry('a', 'binder-11111111'), entry('b', null), entry('c', 'binder-11111111'),
                entry('d', 'binder-99999999')]
  expect(filterMine(feed, 'binder-11111111').map(e => e.card.record_id)).toEqual(['a', 'c'])
  expect(filterMine(feed, '')).toEqual([])
})
