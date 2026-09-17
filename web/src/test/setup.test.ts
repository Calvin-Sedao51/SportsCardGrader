// Pins the Web Storage contract the whole suite relies on. Node 26 (and Node
// 24 with --experimental-webstorage) ships its own localStorage global that
// is unusable without --localstorage-file, and vitest's jsdom environment
// does not replace an existing global — src/test/setup.ts must.
import { beforeEach, expect, test } from 'vitest'

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

test('localStorage and sessionStorage are working Storage objects', () => {
  for (const store of [localStorage, sessionStorage]) {
    expect(typeof store.clear).toBe('function')
    expect(store.getItem('missing')).toBeNull()
    store.setItem('k', 'v')
    expect(store.getItem('k')).toBe('v')
    expect(store.length).toBe(1)
    expect(store.key(0)).toBe('k')
    store.removeItem('k')
    expect(store.getItem('k')).toBeNull()
    expect(store.length).toBe(0)
  }
})

test('the two stores are independent and window sees the same objects', () => {
  localStorage.setItem('only', 'local')
  expect(sessionStorage.getItem('only')).toBeNull()
  expect(window.localStorage.getItem('only')).toBe('local')
  expect(window.sessionStorage).toBe(sessionStorage)
})

test('values are coerced to strings like the real API', () => {
  localStorage.setItem('n', 42 as unknown as string)
  expect(localStorage.getItem('n')).toBe('42')
})
