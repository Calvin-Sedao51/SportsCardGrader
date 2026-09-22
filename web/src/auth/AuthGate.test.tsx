import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import AuthGate from './AuthGate'
import AuthButton from './AuthButton'

const auth = vi.hoisted(() => ({ useAuth: vi.fn() }))
vi.mock('./useAuth', () => ({ useAuth: auth.useAuth }))

const ME = { id: 'u1', email: null, display_name: 'Calvin', hive_display_key: 'binder-abcdef12',
             created_at: '' }

test('AuthGate renders children only when signed in', () => {
  auth.useAuth.mockReturnValue({ status: 'signed_in', user: ME, error: null, configured: true,
                                 signIn: vi.fn(), signOut: vi.fn() })
  render(<AuthGate prompt="Sign in to see your cards."><p>secret</p></AuthGate>)
  expect(screen.getByText('secret')).toBeTruthy()
})

test('AuthGate signed out: prompt + sign-in button that calls signIn, and shows errors', () => {
  const signIn = vi.fn()
  auth.useAuth.mockReturnValue({ status: 'signed_out', user: null, error: 'nope', configured: true,
                                 signIn, signOut: vi.fn() })
  render(<AuthGate prompt="Sign in to see your cards."><p>secret</p></AuthGate>)
  expect(screen.queryByText('secret')).toBeNull()
  expect(screen.getByText(/sign in to see your cards/i)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /sign in with google/i }))
  expect(signIn).toHaveBeenCalledOnce()
  expect(screen.getByRole('alert').textContent).toMatch(/nope/)
})

test('AuthButton toggles between sign in and the signed-in name + sign out', () => {
  const signIn = vi.fn()
  const signOut = vi.fn()
  auth.useAuth.mockReturnValue({ status: 'signed_out', user: null, error: null, configured: true,
                                 signIn, signOut })
  const { rerender } = render(<AuthButton />)
  fireEvent.click(screen.getByRole('button', { name: /^sign in$/i }))
  expect(signIn).toHaveBeenCalledOnce()
  auth.useAuth.mockReturnValue({ status: 'signed_in', user: ME, error: null, configured: true,
                                 signIn, signOut })
  rerender(<AuthButton />)
  expect(screen.getByText('Calvin')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /sign out/i }))
  expect(signOut).toHaveBeenCalledOnce()
})
