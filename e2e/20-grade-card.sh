#!/usr/bin/env bash
# Grade a card: AI settings onboarding → upload front photo → "Scan card" → results.
# Default mocks POST /api/scan with fixtures/scan-result.json (free, instant).
#   E2E_LIVE_AI=1 E2E_AI_KEY=sk-... [E2E_AI_PROVIDER=anthropic|openai]   real model, costs money
#   E2E_CARD_IMAGE=/path/to/photo.jpg                                   use your own card photo
. "$(dirname "$0")/lib.sh"
require_servers
ensure_browser

LIVE="${E2E_LIVE_AI:-0}"
PROVIDER="${E2E_AI_PROVIDER:-anthropic}"
KEY="${E2E_AI_KEY:-}"
IMAGE="${E2E_CARD_IMAGE:-$E2E_DIR/fixtures/card-front.jpg}"
if [ "$LIVE" = 1 ]; then [ -n "$KEY" ] || fail "E2E_LIVE_AI=1 needs E2E_AI_KEY"; else KEY="${KEY:-sk-e2e-mock}"; fi
[ -f "$IMAGE" ] || fail "no image at $IMAGE"

step "AI settings onboarding"
nav "Settings"
expect_visible "page.getByRole('heading', { name: 'Bring your own AI' })"
js "async page => {
  await page.getByLabel('Provider').selectOption($(q "$PROVIDER"));
  await page.getByLabel('API key').fill($(q "$KEY"));
}" >/dev/null || fail "could not fill settings"
click_button "Save"
expect_visible "page.getByRole('button', { name: 'Scan card' })" 5000 "Scan screen after Save"
pass "settings saved ($PROVIDER), landed on Scan"

step "scan ($([ "$LIVE" = 1 ] && echo live AI || echo mocked /api/scan))"
pcli unroute "**/api/scan" >/dev/null 2>&1 || true
if [ "$LIVE" != 1 ]; then
  pcli route "**/api/scan" --content-type=application/json --body="$(cat "$E2E_DIR/fixtures/scan-result.json")" >/dev/null \
    || fail "could not install /api/scan mock"
fi
js "async page => { await page.locator('input[type=file]').nth(0).setInputFiles($(q "$IMAGE")) }" >/dev/null \
  || fail "could not attach front photo"
expect_visible "page.getByAltText('card front')" 5000 "front photo preview"
js "async page => { await page.getByLabel('Asking price (optional)').fill('55') }" >/dev/null
click_button "Scan card"
if [ "$LIVE" = 1 ]; then
  # A real model may reject a synthetic photo ("Photo problem" + Retake): still a full round-trip.
  expect_visible "page.getByRole('button', { name: /^(Scan another|Retake)\$/ })" 95000 "results screen"
  SUBJECT="$(js "async page => {
    const h = page.getByRole('heading', { level: 2 });
    return (await h.count()) ? await h.first().innerText() : 'photo rejected: ' + await page.getByRole('alert').first().innerText();
  }")"
else
  expect_visible "page.getByRole('button', { name: 'Scan another' })" 15000 "results screen"
  SUBJECT="$(js "async page => await page.getByRole('heading', { level: 2 }).first().innerText()")"
fi
pcli screenshot --filename="$ARTIFACTS/grade-result.png" >/dev/null
if [ "$LIVE" != 1 ]; then
  [ "$SUBJECT" = "Luka Doncic" ] || fail "expected mocked subject 'Luka Doncic', got '$SUBJECT'"
  pcli unroute "**/api/scan" >/dev/null
fi
pass "graded: $SUBJECT (screenshot: e2e/artifacts/grade-result.png)"
