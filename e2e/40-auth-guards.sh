#!/usr/bin/env bash
# Sign-in rules around publishing and the Binder:
#   1. signed out: publish is refused client-side, no request is sent
#   2. bad/expired token: server answers 401, UI shows the expiry message
#   3. signed out: Binder → My Collection shows the Google sign-in gate
#   4. Sign out clears the session
#   5. (E2E_RATE=1, server started with PUBLISH_RATE_BUDGET=1) hourly publish limit → 429 message
. "$(dirname "$0")/lib.sh"
require_servers
ensure_browser
[ -s "$AUTH_DIR/session.json" ] || bash "$E2E_DIR/11-token-signin.sh"
GOOD="$(cat "$AUTH_DIR/session.json")"

# Make sure a publishable draft exists (grading itself doesn't need sign-in).
nav "History"
if ! js "async page => (await page.getByRole('button', { name: 'Publish to The Binder' }).count()) > 0" | grep -q true; then
  bash "$E2E_DIR/20-grade-card.sh"
fi
watch_api

try_publish() {
  api_seen_reset
  nav "History"
  js "async page => { await page.getByRole('button', { name: 'Publish to The Binder' }).first().click() }" >/dev/null \
    || fail "no draft to publish"
  js "async page => { await page.getByRole('dialog', { name: 'Publish to The Binder' }).getByRole('button', { name: 'Publish', exact: true }).click() }" >/dev/null
}
expect_alert() {
  expect_visible "page.getByRole('alert').filter({ hasText: $(q "$1") })" 10000 "alert: $1"
}

step "1. signed-out publish is refused"
clear_session
expect_visible "page.getByRole('button', { name: 'Sign in', exact: true })" 5000 "Sign in button"
try_publish
expect_alert "Sign in with Google to publish to the Hive."
[ "$(api_seen /api/publish)" = "[]" ] || fail "a publish request was sent while signed out: $(api_seen /api/publish)"
pass "signed-out publish blocked before any request"

step "2. bad token → 401"
BAD="$(python3 -c 'import json,sys; s=json.loads(sys.argv[1]); s["token"]=s["token"][:-4]+"AAAA"; print(json.dumps(s))' "$GOOD")"
seed_session "$BAD"
try_publish
expect_alert "Your sign-in has expired. Sign in again."
api_seen /api/publish | grep -q '"status":401' || fail "expected a 401 from /api/publish: $(api_seen /api/publish)"
pass "tampered token rejected with 401 and the expiry message"

step "3. My Collection is gated when signed out"
clear_session
nav "Binder"
js "async page => { await page.getByRole('tab', { name: 'My Collection' }).click() }" >/dev/null || fail "no My Collection tab"
expect_visible "page.locator('.auth-gate').getByRole('button', { name: 'Sign in with Google' })" 5000 "auth gate"
pass "My Collection shows the sign-in gate"

step "4. sign out clears the session"
seed_session "$GOOD"
expect_visible "page.getByRole('button', { name: 'Sign out' })" 5000 "signed-in header"
click_button "Sign out"
expect_visible "page.getByRole('button', { name: 'Sign in', exact: true })" 5000 "Sign in after sign-out"
LEFT="$(js "async page => String(await page.evaluate(() => sessionStorage.getItem('cardscanner.auth')))")"
[ "$LEFT" = "null" ] || fail "session still in sessionStorage after sign-out"
js "async page => { await page.reload() }" >/dev/null
expect_visible "page.getByRole('button', { name: 'Sign in', exact: true })" 5000 "still signed out after reload"
pass "sign-out cleared sessionStorage and survives reload"

if [ "${E2E_RATE:-0}" = 1 ]; then
  step "5. hourly publish limit (server must run with PUBLISH_RATE_BUDGET=1)"
  bash "$E2E_DIR/11-token-signin.sh" --fresh   # fresh user = empty budget
  bash "$E2E_DIR/20-grade-card.sh"; bash "$E2E_DIR/20-grade-card.sh"
  try_publish
  expect_visible "page.locator('.chip').filter({ hasText: /In queue|Publishing…|Published/ })" 10000 "first publish accepted"
  try_publish
  expect_alert "hourly publish limit"
  pass "second publish within the hour hit the 429 limit"
fi
seed_session "$GOOD"   # leave the browser signed in for whatever runs next
