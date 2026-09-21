#!/usr/bin/env bash
# Purpose: launchd and health-check helpers shared by install-service.sh and
#          deploy.sh, so there is one implementation of the bootout/bootstrap
#          dance -- getting it wrong takes the live service down. Source it;
#          it is not runnable on its own.

# `launchctl bootout` returns before the job is gone, and bootstrapping the same
# label while it tears down fails with "Bootstrap failed: 5: Input/output error"
# -- after the running service was unloaded, leaving nothing listening. So wait
# for the label to disappear, then retry the bootstrap a few times.
reload_agent() {
  local label="$1" plist="$2" domain="gui/$(id -u)"

  launchctl bootout "$domain/$label" 2>/dev/null || true
  for _ in $(seq 1 40); do
    launchctl print "$domain/$label" >/dev/null 2>&1 || break
    sleep 0.25
  done

  for _ in 1 2 3 4 5; do
    if launchctl bootstrap "$domain" "$plist" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  echo "Could not load $label. Try: launchctl bootstrap $domain $plist" >&2
  return 1
}

# The pid launchd currently has for a label, empty if it is not running.
#
# "Not running" must not be an error: callers run under `set -euo pipefail`,
# and `launchctl print` exits non-zero for a label that is not loaded -- the
# very case where a deploy most needs to keep going.
agent_pid() {
  local out
  out="$(launchctl print "gui/$(id -u)/$1" 2>/dev/null)" || return 0
  printf '%s\n' "$out" \
    | awk -F'=' '/^[[:space:]]*pid[[:space:]]*=/ {gsub(/[^0-9]/, "", $2); print $2; exit}'
}

# Poll a /health URL until it answers. Non-zero on timeout.
wait_for_health() {
  local url="$1" tries="${2:-40}"
  for _ in $(seq 1 "$tries"); do
    curl -fsS -m 1 "$url" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  return 1
}
