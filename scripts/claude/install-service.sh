#!/usr/bin/env bash
# Purpose: run the box tracker permanently and expose it over Tailscale HTTPS.
#          Installs a launchd agent that keeps `moving serve` alive on
#          127.0.0.1:8787, then points `tailscale serve` at it so the tailnet
#          URL is https with a real certificate.
#
#          HTTPS is not cosmetic here: getUserMedia and BarcodeDetector only
#          work in a secure context, so the phone camera cannot work over a
#          plain LAN address.
#
# Usage:   scripts/claude/install-service.sh [--uninstall]
set -euo pipefail

LABEL="ca.toybox.moving"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT=8787
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
UV="$(command -v uv)"

BACKUP_LABEL="$LABEL.backup"
BACKUP_PLIST="$HOME/Library/LaunchAgents/$BACKUP_LABEL.plist"

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootout "gui/$(id -u)/$BACKUP_LABEL" 2>/dev/null || true
  rm -f "$PLIST" "$BACKUP_PLIST"
  tailscale serve --https=443 off 2>/dev/null || true
  echo "Removed both launchd agents and turned off tailscale serve."
  echo "Backups in $REPO/var/backups were left alone."
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$REPO/var/log"

# `launchctl bootout` returns before the job is actually gone, and bootstrapping
# the same label while the old one is still tearing down fails with
# "Bootstrap failed: 5: Input/output error" -- having already unloaded the
# running service. So wait for the label to disappear, then retry.
reload_agent() {
  local label="$1" plist="$2" domain="gui/$(id -u)"

  launchctl bootout "$domain/$label" 2>/dev/null || true
  for _ in $(seq 1 40); do
    launchctl print "$domain/$label" >/dev/null 2>&1 || break
    sleep 0.25
  done

  for attempt in 1 2 3 4 5; do
    if launchctl bootstrap "$domain" "$plist" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  echo "Could not load $label. Try: launchctl bootstrap $domain $plist" >&2
  return 1
}

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$UV</string>
    <string>run</string>
    <string>--project</string><string>$REPO</string>
    <string>moving</string>
    <string>serve</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>$PORT</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$REPO/var/log/moving.out.log</string>
  <key>StandardErrorPath</key><string>$REPO/var/log/moving.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <!-- Homebrew's libzbar is outside the default dyld search path. -->
    <key>DYLD_FALLBACK_LIBRARY_PATH</key><string>/opt/homebrew/lib:/usr/local/lib:/usr/lib</string>
  </dict>
</dict>
</plist>
PLIST_EOF

reload_agent "$LABEL" "$PLIST"

echo "Waiting for the service…"
for _ in $(seq 1 40); do
  curl -fsS -m 1 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -fsS -m 2 "http://127.0.0.1:$PORT/health" >/dev/null \
  || { echo "Service did not come up. See $REPO/var/log/moving.err.log"; exit 1; }
echo "  listening on 127.0.0.1:$PORT"

# --- nightly backup -------------------------------------------------------
# Separate agent rather than a thread in the service: a backup that only runs
# while the web app happens to be healthy is not a backup. StartCalendarInterval
# also catches up after the Mac has been asleep, which a sleep-loop would not.
cat > "$BACKUP_PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$BACKUP_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$UV</string>
    <string>run</string>
    <string>--project</string><string>$REPO</string>
    <string>moving</string>
    <string>backup</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>17</integer></dict>
  <key>StandardOutPath</key><string>$REPO/var/log/backup.out.log</string>
  <key>StandardErrorPath</key><string>$REPO/var/log/backup.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
</dict>
</plist>
PLIST_EOF

reload_agent "$BACKUP_LABEL" "$BACKUP_PLIST"

# Take one now, so the install is proven rather than assumed and there is a
# backup from this moment rather than from 03:17 tomorrow.
echo
"$UV" run --project "$REPO" moving backup

# serve, not funnel: reachable from your own tailnet devices, never the public
# internet.
tailscale serve --bg "$PORT" >/dev/null
echo
tailscale serve status
echo
echo "Installed:"
echo "  $LABEL         starts at login, restarts if it dies"
echo "  $BACKUP_LABEL  nightly at 03:17, keeps 14"
