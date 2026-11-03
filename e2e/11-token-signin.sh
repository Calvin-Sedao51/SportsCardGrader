#!/usr/bin/env bash
# Sign in WITHOUT Google: mint an app token with the server's secret, let the
# server provision the user, seed the browser's sessionStorage, verify the UI.
#   --fresh   brand-new user (models sign-up); default reuses .auth/session.json
. "$(dirname "$0")/lib.sh"
require_servers

step "token sign-in"
SESSION_FILE="$AUTH_DIR/session.json"
if [ "${1:-}" = "--fresh" ] || [ ! -s "$SESSION_FILE" ]; then
  SUB="e2e-$(date +%s)-$RANDOM"
  USERS="$AUTH_DIR/users.json"
  if [ -f "$USERS" ] && grep -q "\"$SUB\"" "$USERS"; then fail "sub $SUB already exists"; fi
  ( cd "$ROOT/server" && .venv/bin/python scripts/mint_test_session.py --sub "$SUB" --api "$API_URL" ) \
    > "$SESSION_FILE.tmp" || fail "minting a session failed"
  mv "$SESSION_FILE.tmp" "$SESSION_FILE"
  grep -q "\"$SUB\"" "$USERS" 2>/dev/null && pass "sign-up: server created user record for $SUB" \
    || fail "sign-up: no user record for $SUB in $USERS"
fi
SESSION_JSON="$(cat "$SESSION_FILE")"
NAME="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["user"]["display_name"])' <<<"$SESSION_JSON")"

ensure_browser
seed_session "$SESSION_JSON"
expect_visible "page.getByRole('button', { name: 'Sign out' })" 10000 "Sign out button"
WHO="$(js "async page => await page.locator('.auth-status .who').innerText()")"
[ "$WHO" = "$NAME" ] || fail "header shows '$WHO', expected '$NAME'"
pass "signed in as $NAME (token injection)"
