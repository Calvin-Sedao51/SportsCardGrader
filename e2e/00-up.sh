#!/usr/bin/env bash
# Start the API (:8000) and web app (:5173) with test-safe env, then wait for both.
#   bash e2e/00-up.sh          start (no-op for anything already listening)
#   bash e2e/00-up.sh --down   stop what this script started
. "$(dirname "$0")/lib.sh"

PIDS="$AUTH_DIR/pids"
if [ "${1:-}" = "--down" ]; then
  [ -f "$PIDS" ] && while read -r pid; do pkill -P "$pid" 2>/dev/null || true; kill "$pid" 2>/dev/null || true; done < "$PIDS"
  rm -f "$PIDS"; echo "stopped"; exit 0
fi

# Real Google sign-in needs a real OAuth client ID on BOTH sides; otherwise a
# placeholder keeps the auth endpoints enabled for token-injection runs.
CLIENT_ID="${E2E_GOOGLE_CLIENT_ID:-e2e-placeholder.apps.googleusercontent.com}"

if ! curl -sf "$API_URL/api/health" >/dev/null; then
  (
    cd "$ROOT/server"
    export AUTH_GOOGLE_CLIENT_ID="$CLIENT_ID" AUTH_JWT_SECRET
    export AUTH_USERS_FILE="$AUTH_DIR/users.json"
    # Dry run: publishes are logged, never broadcast to Hive.
    export HIVE_DRY_RUN=true HIVE_ACCOUNT="${HIVE_ACCOUNT:-e2e-test}" HIVE_POSTING_KEY="${HIVE_POSTING_KEY:-dry-run-dummy}"
    # Dry run never spends Resource Credits, so do not park jobs on the placeholder account's 0% RC.
    export HIVE_QUEUE_DIR="$AUTH_DIR/queue" HIVE_ROOT_POST_INTERVAL_SECONDS=0 HIVE_MIN_RC_PERCENT=0
    export PUBLISH_RATE_BUDGET="${PUBLISH_RATE_BUDGET:-50}"
    exec .venv/bin/uvicorn app.main:app --port 8000
  ) > "$ARTIFACTS/server.log" 2>&1 &
  echo $! >> "$PIDS"
fi

if ! curl -sf "$BASE_URL" >/dev/null; then
  # Same ID as the server so the web build shows its sign-in controls; the
  # placeholder only matters if someone clicks "Sign in" during a token run.
  export VITE_GOOGLE_CLIENT_ID="$CLIENT_ID"
  ( cd "$ROOT/web" && exec npm run dev -- --port 5173 --strictPort ) > "$ARTIFACTS/web.log" 2>&1 &
  echo $! >> "$PIDS"
fi

for i in $(seq 1 60); do
  curl -sf "$API_URL/api/health" >/dev/null && curl -sf "$BASE_URL" >/dev/null && { pass "servers up ($API_URL, $BASE_URL)"; exit 0; }
  sleep 1
done
fail "servers did not come up — see e2e/artifacts/server.log and web.log"
