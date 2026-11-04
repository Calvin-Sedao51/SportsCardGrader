#!/usr/bin/env bash
# Install a user LaunchAgent that runs the sports card DB sync daily at 06:30.
#
#   server/scripts/install_sportsdb_launchd.sh             # install (idempotent)
#   server/scripts/install_sportsdb_launchd.sh --run-now   # install + run once now
#   server/scripts/install_sportsdb_launchd.sh --uninstall
#   server/scripts/install_sportsdb_launchd.sh --dry-run   # print the plist only
#
# Logs: server/data/sportsdb-logs/sync.{out,err}.log (stdout is the --json
# summary of each run; stderr is the per-source progress log).
set -euo pipefail

LABEL="com.cardscanner.sportsdb-sync"
SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$SERVER_DIR/.venv/bin/python"
LOG_DIR="$SERVER_DIR/data/sportsdb-logs"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

plist() {
  cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>-m</string>
    <string>app.sportsdb</string>
    <string>sync</string>
    <string>--json</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$SERVER_DIR</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>6</integer>
    <key>Minute</key>
    <integer>30</integer>
  </dict>
  <key>RunAtLoad</key>
  <false/>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/sync.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/sync.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin</string>
  </dict>
</dict>
</plist>
PLIST
}

case "${1:-}" in
  --dry-run)
    plist
    exit 0
    ;;
  --uninstall)
    launchctl bootout "$DOMAIN" "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed $LABEL ($PLIST)."
    exit 0
    ;;
  ""|--run-now)
    ;;
  *)
    echo "usage: $0 [--run-now|--uninstall|--dry-run]" >&2
    exit 2
    ;;
esac

[ -x "$PYTHON" ] || { echo "No venv at $PYTHON — run 'make setup' first." >&2; exit 1; }

mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"
plist > "$PLIST"
# bootout fails when the job isn't loaded; that's the fresh-install case.
launchctl bootout "$DOMAIN" "$PLIST" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "Installed $LABEL: daily at 06:30 via $PLIST"
echo "  logs: $LOG_DIR/sync.out.log (JSON summaries), $LOG_DIR/sync.err.log (progress)"
echo "  status: launchctl print $DOMAIN/$LABEL | head"

if [ "${1:-}" = "--run-now" ]; then
  launchctl kickstart -k "$DOMAIN/$LABEL"
  echo "Started a run now; follow it with: tail -f $LOG_DIR/sync.err.log"
fi
