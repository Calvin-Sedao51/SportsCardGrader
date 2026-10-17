import type { ReactNode } from 'react'
import { SIGN_IN_UNAVAILABLE_NOTE, useAuth } from './useAuth'

// Renders children only for a signed-in user; otherwise a sign-in prompt, or
// a short note when sign-in is not configured on this build.
export default function AuthGate({ prompt, children }: { prompt: string; children: ReactNode }) {
  const { status, error, configured, signIn } = useAuth()
  if (status === 'signed_in') return <>{children}</>
  if (!configured) {
    return (
      <div className="auth-gate">
        <p className="caption">{SIGN_IN_UNAVAILABLE_NOTE}</p>
      </div>
    )
  }
  return (
    <div className="auth-gate">
      <p>{prompt}</p>
      <button onClick={() => { signIn() }}>
        {status === 'signing_in' ? 'Signing in…' : 'Sign in with Google'}
      </button>
      {error && <p role="alert" className="caption">{error}</p>}
    </div>
  )
}
