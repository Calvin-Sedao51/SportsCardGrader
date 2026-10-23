#!/usr/bin/env bash
# Real Google sign-up / sign-in in a headed, persistent Chrome profile.
# You complete the Google prompt by hand; the script verifies the result and
# saves the app session to .auth/session.json for the other scripts.
# Needs E2E_GOOGLE_CLIENT_ID (see README) and servers started with it.
. "$(dirname "$0")/lib.sh"
require_servers
[ -n "${E2E_GOOGLE_CLIENT_ID:-}" ] || fail "set E2E_GOOGLE_CLIENT_ID in e2e/.env, then restart: 00-up.sh --down && 00-up.sh"

step "Google sign-in (manual)"
# Google refuses sign-in in obviously automated browsers more often with the
# bundled Chromium; real Chrome + a persistent profile keeps the Google login.
if session_open; then pcli close >/dev/null; fi
GBROWSER="${E2E_BROWSER:-}"
if [ -z "$GBROWSER" ] && [ -d "/Applications/Google Chrome.app" ]; then GBROWSER=chrome; fi
[ -n "$GBROWSER" ] || echo "  note: Google Chrome not installed — using bundled Chromium; if Google says the browser may not be secure, install Chrome (npx playwright install chrome) and rerun."
pcli open "$BASE_URL" --headed --persistent ${GBROWSER:+--browser="$GBROWSER"} >/dev/null

if js "async page => (await page.getByRole('button', { name: 'Sign out' }).count()) > 0" | grep -q true; then
  click_button "Sign out"
fi
# Record whether the server created the account (sign-up) or found it (sign-in).
js "async page => {
  page.on('response', async r => {
    if (!r.url().endsWith('/api/auth/google')) return;
    const body = await r.json().catch(() => ({}));
    await page.evaluate(c => { window.__e2eCreated = String(c) }, body.created).catch(() => {});
  });
}" >/dev/null
click_button "Sign in"
echo
echo "  ▶ Finish the Google prompt in the browser window (choose the account, consent)."
echo "    If One Tap does not appear, a popup or the Sign in button's error explains why."
read -r -p "  Press Enter once the header shows your name… " _

expect_visible "page.getByRole('button', { name: 'Sign out' })" 15000 "Sign out button after Google sign-in"
js "async page => await page.evaluate(() => sessionStorage.getItem('cardscanner.auth'))" > "$AUTH_DIR/session.json"
cp "$AUTH_DIR/session.json" "$AUTH_DIR/google-session.json"

CREATED="$(js "async page => await page.evaluate(() => window.__e2eCreated ?? 'unknown')")"
NAME="$(js "async page => await page.locator('.auth-status .who').innerText()")"
pass "signed in with Google as $NAME (session saved to e2e/.auth/session.json)"
case "$CREATED" in
  true)  pass "sign-up: first sign-in for this Google account created a user" ;;
  false) pass "sign-in: existing user (use a fresh Google account to exercise sign-up)" ;;
  *)     echo "  (could not read 'created' from /api/auth/google; see: playwright-cli -s=$SESSION requests)" ;;
esac
