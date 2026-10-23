import { useAuth } from './useAuth'

// Header control: "Sign in" when signed out, name + "Sign out" when signed in.
// With no sign-in provider on this build the signed-out state renders nothing;
// an existing session (seeded, or from another provider) still gets Sign out.
export default function AuthButton() {
  const { status, user, error, configured, signIn, signOut } = useAuth()
  if (status === 'signed_in' && user) {
    return (
      <div className="auth-status">
        <span className="who" title={user.hive_display_key}>{user.display_name}</span>
        <button onClick={signOut}>Sign out</button>
      </div>
    )
  }
  if (!configured) return null
  return (
    <div className="auth-status">
      <button onClick={() => { signIn() }}>
        {status === 'signing_in' ? 'Signing in…' : 'Sign in'}
      </button>
      {error && <span role="alert" className="caption">{error}</span>}
    </div>
  )
}
