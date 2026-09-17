import { beforeEach, expect, test } from 'vitest'
import { clearSession, getToken, loadSession, saveSession } from './session'
import type { AuthSession } from './session'

const SESSION: AuthSession = {
  token: 'app.jwt.token',
  user: { id: 'u1', email: 'calvin@example.com', display_name: 'Calvin',
          hive_display_key: 'binder-abcdef12', created_at: '2026-09-16T00:00:00Z' },
}

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
  clearSession()
})

test('starts signed out', () => {
  expect(loadSession()).toBeNull()
  expect(getToken()).toBeNull()
})

test('save -> load round trip, token available for headers', () => {
  saveSession(SESSION)
  expect(loadSession()).toEqual(SESSION)
  expect(getToken()).toBe('app.jwt.token')
})

test('session survives a module-level reset only via sessionStorage', () => {
  saveSession(SESSION)
  expect(JSON.parse(sessionStorage.getItem('cardscanner.auth')!)).toEqual(SESSION)
  // Never in localStorage: that is where the AI key lives.
  expect(Object.keys(localStorage)).toEqual([])
})

test('clear removes both memory and sessionStorage copies', () => {
  saveSession(SESSION)
  clearSession()
  expect(loadSession()).toBeNull()
  expect(sessionStorage.getItem('cardscanner.auth')).toBeNull()
})

test('corrupt sessionStorage reads as signed out', () => {
  sessionStorage.setItem('cardscanner.auth', '{not json')
  expect(loadSession()).toBeNull()
})
