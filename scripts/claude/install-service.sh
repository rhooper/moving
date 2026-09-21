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

# CDPATH is set in this user's shell, and a relative `cd` through it prints its
# target into the surrounding $( ), corrupting `$(cd ... && pwd)`.
CDPATH=""

# MOVING_SERVICE_LABEL / MOVING_SERVICE_PORT install a throwaway second instance
# beside the real one, to test the deploy path. It must not touch `tailscale
# serve`: there is a single :443 handler for the whole machine.
DEFAULT_LABEL="ca.toybox.moving"
DEFAULT_PORT=8787
LABEL="${MOVING_SERVICE_LABEL:-$DEFAULT_LABEL}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT="${MOVING_SERVICE_PORT:-$DEFAULT_PORT}"
IS_DEFAULT_INSTANCE=0
if [[ "$LABEL" == "$DEFAULT_LABEL" && "$PORT" == "$DEFAULT_PORT" ]]; then
  IS_DEFAULT_INSTANCE=1
fi
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
UV="$(command -v uv)"

# reload_agent / wait_for_health live in one place, shared with deploy.sh.
# shellcheck source=lib/launchd.sh
source "$REPO/scripts/claude/lib/launchd.sh"

# The service must be told to use the real printer. The library default is
# `fake`, which writes a PNG preview and returns success -- so without this the
# Print button appears to work and no tape ever comes out.
PRINTER_BACKEND="${MOVING_PRINTER_BACKEND:-brother_ql}"

BACKUP_LABEL="$LABEL.backup"
BACKUP_PLIST="$HOME/Library/LaunchAgents/$BACKUP_LABEL.plist"

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootout "gui/$(id -u)/$BACKUP_LABEL" 2>/dev/null || true
  rm -f "$PLIST" "$BACKUP_PLIST"
  # The hooks are this checkout's own (install-hooks.sh resolves them from
  # $REPO), so removing them is right for any instance. tailscale serve is not:
  # there is one :443 handler for the machine and it belongs to the real one.
  "$REPO/scripts/claude/install-hooks.sh" --uninstall || true
  if (( IS_DEFAULT_INSTANCE )); then
    tailscale serve --https=443 off 2>/dev/null || true
    echo "Removed both launchd agents, the post-merge hook, and tailscale serve."
  else
    echo "Removed both $LABEL agents and the post-merge hook."
    echo "tailscale serve belongs to the default instance and was left alone."
  fi
  echo "Backups in $REPO/var/backups were left alone."
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$REPO/var/log"

# Record what is being installed so /health can report it, and so the first
# `deploy.sh --if-changed` after an install has something to compare against.
git -C "$REPO" rev-parse HEAD > "$REPO/var/deployed-revision" 2>/dev/null || true

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
    <!-- Without this the app falls back to the \`fake\` backend and silently
         writes preview PNGs instead of printing. The backticks are escaped
         because this heredoc is unquoted: bare ones ran \`fake\` as a command
         and dropped the word from the comment. -->
    <key>MOVING_PRINTER_BACKEND</key><string>$PRINTER_BACKEND</string>
  </dict>
</dict>
</plist>
PLIST_EOF

reload_agent "$LABEL" "$PLIST"

echo "Waiting for the service…"
wait_for_health "http://127.0.0.1:$PORT/health" 40 \
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

# serve, not funnel: tailnet devices only, never the public internet. Default
# instance only: pointing the one :443 handler at a second instance would take
# the real one off the URL printed on every label.
if (( IS_DEFAULT_INSTANCE )); then
  tailscale serve --bg "$PORT" >/dev/null
  echo
  tailscale serve status
  echo
else
  echo
  echo "Non-default instance ($LABEL on $PORT): tailscale serve left as it is."
  echo
fi
# --- printer ---------------------------------------------------------------
echo
echo "Printer backend: $PRINTER_BACKEND"
if "$UV" run --project "$REPO" python -c "
import sys, usb.core
sys.exit(0 if usb.core.find(idVendor=0x04f9) is not None else 1)
" 2>/dev/null; then
  echo "  QL-800 found on USB."
  echo "  If it powers itself off, turn that off once in Brother's Printer"
  echo "  Setting Tool: Device Settings > Basic > Auto Power Off (AC/DC) > None."
  echo "  It is stored in the printer, so it only needs doing once. There is no"
  echo "  way to set it over USB."
else
  echo "  No Brother printer found on USB (looked for vendor 0x04f9)."
  echo "  Printing will fail until it is plugged in and switched on, with"
  echo "  Editor Lite mode OFF."
fi

# --- automatic redeploys ---------------------------------------------------
# .git/hooks is not version-controlled, so the post-merge hook is installed here.
echo
"$REPO/scripts/claude/install-hooks.sh"

echo
echo "Installed:"
echo "  $LABEL         starts at login, restarts if it dies"
echo "  $BACKUP_LABEL  nightly at 03:17, keeps 14"
echo "  post-merge hook        a merge that moves main runs scripts/claude/deploy.sh"
