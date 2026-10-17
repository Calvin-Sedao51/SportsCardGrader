#!/usr/bin/env bash
# Publish the newest graded scan: History → "Publish to The Binder" → consent → job completes.
# Needs a signed-in session (10/11) and a draft scan (20). Server runs HIVE_DRY_RUN=true,
# so nothing reaches the chain — and therefore the card won't appear in the live Community feed.
. "$(dirname "$0")/lib.sh"
require_servers
ensure_browser
expect_visible "page.getByRole('button', { name: 'Sign out' })" 5000 "signed-in header (run 11-token-signin.sh first)"

step "publish to The Binder"
watch_api; api_seen_reset
nav "History"
expect_visible "page.getByRole('button', { name: 'Publish to The Binder' })" 10000 "a publishable draft (run 20-grade-card.sh first)"
BEFORE="$(js "async page => await page.getByRole('button', { name: 'Publish to The Binder' }).count()")"
PUBLISHED_BEFORE="$(js "async page => await page.locator('.chip-published').count()")"
js "async page => { await page.getByRole('button', { name: 'Publish to The Binder' }).first().click() }" >/dev/null
expect_visible "page.getByRole('dialog', { name: 'Publish to The Binder' })" 5000 "consent dialog"
js "async page => { await page.getByRole('dialog', { name: 'Publish to The Binder' }).getByRole('button', { name: 'Publish', exact: true }).click() }" >/dev/null \
  || fail "could not confirm publish"

expect_visible "page.locator('.chip').filter({ hasText: /In queue|Publishing…|Published/ })" 10000 "queued/published chip"
SEEN="$(api_seen /api/publish)"
python3 -c '
import json, sys
calls = [c for c in json.loads(sys.argv[1]) if c["method"] == "POST"]
assert calls, "no POST /api/publish seen"
c = calls[-1]
assert c["bearer"], "POST /api/publish had no Bearer token"
assert 200 <= c["status"] < 300, "POST /api/publish -> %s" % c["status"]
' "$SEEN" || fail "publish request check failed: $SEEN"
pass "POST /api/publish sent with Bearer token, accepted"

# Earlier scans in this browser may already be Published: wait for the count to grow.
# The dry-run worker confirms one job at a time (~15s each), so allow for a backlog.
js "async page => { await page.waitForFunction(n => document.querySelectorAll('.chip-published').length > n, $PUBLISHED_BEFORE, { timeout: 120000 }) }" >/dev/null \
  || fail "this card never reached Published (dry-run worker; see e2e/artifacts/server.log)"
AFTER="$(js "async page => await page.getByRole('button', { name: 'Publish to The Binder' }).count()")"
[ "$AFTER" -lt "$BEFORE" ] || fail "draft still publishable after publishing ($BEFORE → $AFTER)"
pcli screenshot --filename="$ARTIFACTS/published.png" >/dev/null
pass "card published (dry run) — screenshot: e2e/artifacts/published.png"
