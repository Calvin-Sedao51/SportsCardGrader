import type { ReactNode } from 'react'
import { useAuth } from './useAuth'

// Renders children only for a signed-in user; otherwise a sign-in prompt.
export default function AuthGate({ prompt, children }: { prompt: string; children: ReactNode }) {
  const { status, error, signIn } = useAuth()
  if (status === 'signed_in') return <>{children}</>
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
