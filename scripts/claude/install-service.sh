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

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  tailscale serve --https=443 off 2>/dev/null || true
  echo "Removed the launchd agent and turned off tailscale serve."
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$REPO/var/log"

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

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "Waiting for the service…"
for _ in $(seq 1 40); do
  curl -fsS -m 1 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -fsS -m 2 "http://127.0.0.1:$PORT/health" >/dev/null \
  || { echo "Service did not come up. See $REPO/var/log/moving.err.log"; exit 1; }
echo "  listening on 127.0.0.1:$PORT"

# serve, not funnel: reachable from your own tailnet devices, never the public
# internet.
tailscale serve --bg "$PORT" >/dev/null
echo
tailscale serve status
echo
echo "Installed. It now starts at login and restarts if it dies."
