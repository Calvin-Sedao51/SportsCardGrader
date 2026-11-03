#!/usr/bin/env bash
# Run every flow in one headed browser:
#   bash e2e/run-all.sh            token sign-up/sign-in → grade → publish → sign-in rules
#   bash e2e/run-all.sh --google   real Google sign-in (manual step) instead of token injection
#   --keep                         leave the browser open at the end
. "$(dirname "$0")/lib.sh"
GOOGLE=0; KEEP=0
for a in "$@"; do case "$a" in --google) GOOGLE=1 ;; --keep) KEEP=1 ;; esac; done

bash "$E2E_DIR/00-up.sh"
# Start from a clean in-memory profile: leftover scans/consents from earlier runs
# (IndexedDB) would otherwise leak into the checks.
if [ "$GOOGLE" = 0 ] && session_open; then pcli close >/dev/null; fi
if [ "$GOOGLE" = 1 ]; then bash "$E2E_DIR/10-google-signin.sh"; else bash "$E2E_DIR/11-token-signin.sh" --fresh; fi
bash "$E2E_DIR/20-grade-card.sh"
bash "$E2E_DIR/30-publish.sh"
bash "$E2E_DIR/40-auth-guards.sh"
printf '\n\033[32mALL FLOWS PASSED\033[0m\n'
[ "$KEEP" = 1 ] || pcli close >/dev/null
