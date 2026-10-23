# Shared helpers for the headed playwright-cli automations. Source, don't run.
# shellcheck shell=bash
set -euo pipefail

E2E_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$E2E_DIR/.." && pwd)"
[ -f "$E2E_DIR/.env" ] && { set -a; . "$E2E_DIR/.env"; set +a; }

SESSION="${E2E_SESSION:-cardgrader}"
BASE_URL="${E2E_BASE_URL:-http://localhost:5173}"
API_URL="${E2E_API_URL:-http://localhost:8000}"
BROWSER="${E2E_BROWSER:-}"   # empty = Playwright's bundled Chromium
AUTH_DIR="$E2E_DIR/.auth"
ARTIFACTS="$E2E_DIR/artifacts"
# Must match the server started by 00-up.sh; local test secret, never production.
export AUTH_JWT_SECRET="${AUTH_JWT_SECRET:-e2e-local-secret-do-not-deploy}"
mkdir -p "$AUTH_DIR" "$ARTIFACTS"

# All snapshots/logs land in e2e/.playwright-cli regardless of caller cwd.
cd "$E2E_DIR"

pcli() { playwright-cli -s="$SESSION" "$@"; }
# run a JS function `async page => {...}` in the browser session; non-zero on throw
# (string results come back JSON-quoted from --raw; unquote them)
js() {
  local out
  out="$(pcli --raw run-code "$1")" || { printf '%s\n' "$out" >&2; return 1; }
  python3 -c '
import json, sys
s = sys.argv[1]
try:
    v = json.loads(s)
    print(v if isinstance(v, str) else s)
except ValueError:
    print(s)' "$out"
}

pass() { printf '\033[32mPASS\033[0m %s\n' "$*"; }
fail() {
  printf '\033[31mFAIL\033[0m %s\n' "$*" >&2
  pcli screenshot --filename="$ARTIFACTS/fail-$(date +%s).png" >/dev/null 2>&1 || true
  exit 1
}
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# JSON-encode a bash string for safe embedding in run-code snippets.
q() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"; }

session_open() {
  playwright-cli list --json 2>/dev/null | python3 -c '
import json, sys
names = [b["name"] for b in json.load(sys.stdin).get("browsers", []) if b.get("status") == "open"]
sys.exit(0 if sys.argv[1] in names else 1)' "$SESSION"
}

# Open the headed browser once; later scripts reuse it. Extra args go to `open`.
ensure_browser() {
  if session_open; then
    pcli goto "$BASE_URL" >/dev/null
  else
    pcli open "$BASE_URL" --headed ${BROWSER:+--browser="$BROWSER"} "$@" >/dev/null
  fi
}

# expect_visible <playwright-locator-expression> [timeout_ms] [label]
#   e.g. expect_visible "page.getByRole('button', { name: 'Sign out' })"
expect_visible() {
  local loc="$1" timeout="${2:-10000}" label="${3:-$1}"
  js "async page => { await $loc.first().waitFor({ state: 'visible', timeout: $timeout }) }" >/dev/null \
    || fail "not visible: $label"
}

click_button() { js "async page => { await page.getByRole('button', { name: $(q "$1"), exact: true }).first().click() }" >/dev/null \
  || fail "could not click button: $1"; }

nav() { click_button "$1"; }

require_servers() {
  curl -sf "$API_URL/api/health" >/dev/null || fail "API not reachable at $API_URL — run e2e/00-up.sh"
  curl -sf "$BASE_URL" >/dev/null || fail "web app not reachable at $BASE_URL — run e2e/00-up.sh"
}

# Put a {token,user} session JSON into the app's sessionStorage and reload.
seed_session() {
  local json="$1"
  js "async page => {
    await page.evaluate(v => sessionStorage.setItem('cardscanner.auth', v), $(q "$json"));
    await page.reload();
  }" >/dev/null || fail "could not seed session"
}

clear_session() {
  js "async page => { await page.evaluate(() => sessionStorage.removeItem('cardscanner.auth')); await page.reload(); }" >/dev/null
}

# Record API calls the page makes so scripts can assert on status + auth header.
# After install, read with: api_seen '/api/publish'  →  JSON list of {method,status,bearer}
watch_api() {
  js "async page => {
    if (page.__e2eWatching) return;
    page.__e2eWatching = true;
    page.__e2eSeen = [];
    page.on('response', r => {
      const u = new URL(r.url());
      if (!u.pathname.startsWith('/api/')) return;
      const auth = r.request().headers()['authorization'] || '';
      page.__e2eSeen.push({ path: u.pathname, method: r.request().method(), status: r.status(), bearer: auth.startsWith('Bearer ') });
    });
  }" >/dev/null
}
api_seen() { js "async page => JSON.stringify((page.__e2eSeen || []).filter(x => x.path === $(q "$1")))"; }
api_seen_reset() { js "async page => { page.__e2eSeen = [] }" >/dev/null; }
