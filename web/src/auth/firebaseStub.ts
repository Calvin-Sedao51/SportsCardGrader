// Firebase is NOT a dependency of this app (it is not in package.json and
// could not be added offline). This stub is the documented dev fallback
// behind the same googleSignIn(callback) interface: it never produces a
// credential, so the app runs fully signed-out — scanning, publishing
// anonymously and browsing the community feed all work as before.
//
// To wire real Firebase Google sign-in later: `npm install firebase`, then
// replace this module's export with
//   signInWithPopup(getAuth(app), new GoogleAuthProvider())
//     .then(r => callback(GoogleAuthProvider.credentialFromResult(r)!.idToken!))
// The server's POST /api/auth/google accepts that Google ID token unchanged.

export async function googleSignIn(_callback: (credential: string) => void): Promise<boolean> {
  console.warn(
    'Google sign-in is not configured on this build (no VITE_GOOGLE_CLIENT_ID and no Firebase). '
    + 'Sign-in is a no-op; the app keeps working signed out.')
  return false
}
