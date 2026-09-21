#!/usr/bin/env bash
# Purpose: put what is on main into the live launchd service. The post-merge
#          hook (scripts/claude/install-hooks.sh) runs it whenever a merge
#          moves main in the main checkout.
#
# Usage:   scripts/claude/deploy.sh [--if-changed]
#
#          --if-changed  do nothing, silently, if the running service is
#                        already healthy on this exact commit. This is what the
#                        git hook passes.
#
# Order: guards -> backup -> uv sync -> tests -> restart -> health -> revision
#
#   The backup comes before the tests: the merge has already put any new
#   migration on disk, and db.migrate() re-reads that directory on every
#   connection, so the *old* process applies it to the live database on its
#   next request. Nothing touches the running service until the tests are
#   green; a failed deploy leaves the old build serving.
set -euo pipefail

# CDPATH is set in this user's shell, and a `cd` that resolves a relative path
# through it prints the destination: `$(cd ... && pwd -P)` then returns two
# lines and every path comparison below silently fails.
CDPATH=""

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=lib/launchd.sh
source "$REPO/scripts/claude/lib/launchd.sh"

LABEL="${MOVING_SERVICE_LABEL:-ca.toybox.moving}"
PORT="${MOVING_SERVICE_PORT:-8787}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
HEALTH="http://127.0.0.1:$PORT/health"
# Read once, at startup, by the service.
REVISION_FILE="$REPO/var/deployed-revision"

IF_CHANGED=0
case "${1:-}" in
  --if-changed) IF_CHANGED=1 ;;
  "") ;;
  *) echo "Usage: scripts/claude/deploy.sh [--if-changed]" >&2; exit 2 ;;
esac

# Hooks hand their child the git environment of the command that triggered them.
# GIT_DIR in particular would make every check below describe the merge instead
# of this checkout, so drop them before looking at anything.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_QUARANTINE_PATH || true

# A hook launched from a GUI git client gets a minimal PATH, and uv lives in
# Homebrew's prefix.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31mDEPLOY ABORTED\033[0m: %s\n' "$*" >&2; exit 1; }
loud() { printf '\n\033[1;31m%s\033[0m\n' "$*" >&2; }

cd "$REPO"

# --- guard rails ----------------------------------------------------------
# Deploying the wrong tree is worse than not deploying: refuse, never guess.

command -v git >/dev/null 2>&1 || die "git is not on PATH"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "$REPO is not a git checkout"

# Never from a worktree: worktrees get deleted, and the agent serves the main
# checkout. Hooks live in the common .git, so they fire for worktrees too.
GIT_DIR_ABS="$(git rev-parse --absolute-git-dir)"
COMMON_ABS="$(cd "$(git rev-parse --git-common-dir)" && pwd -P)"
if [[ "$GIT_DIR_ABS" != "$COMMON_ABS" ]]; then
  die "refusing to deploy from the linked worktree $REPO
                Deploy from the main checkout: $(dirname "$COMMON_ABS")"
fi

BRANCH="$(git symbolic-ref --quiet --short HEAD || true)"
if [[ "$BRANCH" != "main" ]]; then
  # An `x && y` list, not an if, would be the last command run and `set -e`
  # would take the script out before die() ever printed.
  WHERE="a detached HEAD"
  if [[ -n "$BRANCH" ]]; then WHERE="branch $BRANCH"; fi
  die "refusing to deploy from $WHERE -- only main is deployed"
fi

# Untracked files are ignored on purpose (var/, scratch notes); modified or
# staged tracked files are not, because they are code the deploy would ship
# without it ever having been committed or reviewed.
DIRTY="$(git status --porcelain --untracked-files=no)"
if [[ -n "$DIRTY" ]]; then
  die "refusing to deploy a dirty working tree:
$DIRTY"
fi

[[ -f "$PLIST" ]] \
  || die "no launchd agent at $PLIST -- run scripts/claude/install-service.sh first"

# The agent must be the one that serves *this* checkout. Without this, running
# deploy.sh out of a copy of the repo would restart the real service while
# proving its health against a tree nobody is serving.
PLIST_REPO="$(/usr/libexec/PlistBuddy -c 'Print :WorkingDirectory' "$PLIST" 2>/dev/null || true)"
if [[ -n "$PLIST_REPO" && "$PLIST_REPO" != "$REPO" ]]; then
  die "the $LABEL agent serves $PLIST_REPO, not $REPO
                Deploy from there, or re-run scripts/claude/install-service.sh here."
fi

command -v uv >/dev/null 2>&1 || die "uv is not on PATH"
UV="$(command -v uv)"

# --- what we are deploying ------------------------------------------------
REVISION="$(git rev-parse HEAD)"
SHORT="$(git rev-parse --short HEAD)"
SUBJECT="$(git log -1 --pretty=%s)"
PREVIOUS="$(cat "$REVISION_FILE" 2>/dev/null || true)"
PREVIOUS_SHORT="${PREVIOUS:0:12}"

# --if-changed also insists the service is actually answering: "already on this
# commit" is not a reason to walk away from a service that is down.
if (( IF_CHANGED )) && [[ "$PREVIOUS" == "$REVISION" ]] \
   && curl -fsS -m 2 "$HEALTH" >/dev/null 2>&1; then
  exit 0
fi

echo "Deploying $SHORT  $SUBJECT"
echo "  repo     $REPO"
echo "  agent    $LABEL  (port $PORT)"
echo "  running  ${PREVIOUS_SHORT:-unrecorded}"

# --- backup, before any migration can run ---------------------------------
say "Backup"
if ! BACKUP_OUT="$("$UV" run --project "$REPO" moving backup)"; then
  die "backup failed -- nothing else was touched, the old build is still serving"
fi
echo "$BACKUP_OUT"
BACKUP_PATH="$(printf '%s\n' "$BACKUP_OUT" | awk 'NR==1{print $1}')"

# --- dependencies ---------------------------------------------------------
say "uv sync"
"$UV" sync --project "$REPO" || die "uv sync failed -- the old build is still serving"

# --- tests: the gate ------------------------------------------------------
say "Tests"
if ! "$UV" run --project "$REPO" pytest -q; then
  loud "TESTS FAILED -- NOT DEPLOYING."
  loud "The service was not touched. It is still serving ${PREVIOUS_SHORT:-the previous build}."
  loud "Fix main (or revert the merge), then run scripts/claude/deploy.sh again."
  exit 1
fi

# --- restart --------------------------------------------------------------
# Written before the restart: the service reads it once at startup, which makes
# /health's answer proof that this process runs this code.
say "Restart"
mkdir -p "$REPO/var"
printf '%s\n' "$REVISION" > "$REVISION_FILE"

OLD_PID="$(agent_pid "$LABEL")"
if ! reload_agent "$LABEL" "$PLIST"; then
  loud "launchctl would not load $LABEL; trying once more."
  reload_agent "$LABEL" "$PLIST" || die "could not load $LABEL -- the service is DOWN.
                Recover with: launchctl bootstrap gui/$(id -u) $PLIST"
fi

if ! wait_for_health "$HEALTH" 60; then
  loud "$LABEL did not come up on $HEALTH; reloading once more."
  reload_agent "$LABEL" "$PLIST" || true
  if ! wait_for_health "$HEALTH" 60; then
    echo "--- tail of $REPO/var/log/moving.err.log ---" >&2
    tail -n 25 "$REPO/var/log/moving.err.log" 2>/dev/null >&2 || true
    die "the service is not answering $HEALTH.
                main is at $SHORT; the previously deployed commit was ${PREVIOUS_SHORT:-unknown}.
                To go back: git reset --hard ${PREVIOUS:-<previous commit>} && scripts/claude/deploy.sh
                (check $BACKUP_PATH first if a migration ran)."
  fi
fi

# --- prove the new code is what answered ----------------------------------
# Tells "the new build is serving" from "something is serving".
SERVED_JSON="$(curl -fsS -m 5 "$HEALTH")" || die "$HEALTH stopped answering mid-check"
SERVED="$(printf '%s' "$SERVED_JSON" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("revision", ""))' || true)"
if [[ "$SERVED" != "$REVISION" ]]; then
  die "the service is up but reports revision '${SERVED:-none}', not $SHORT.
                Something else is listening on $PORT, or the restart did not take."
fi

NEW_PID="$(agent_pid "$LABEL")"

printf '\n\033[1mDeployed.\033[0m\n'
echo "  revision  $SHORT  $SUBJECT"
echo "  previous  ${PREVIOUS_SHORT:-unrecorded}"
echo "  agent     $LABEL  pid ${OLD_PID:-none} -> ${NEW_PID:-unknown}"
echo "  health    $HEALTH  ok, revision $SHORT"
echo "  backup    ${BACKUP_PATH:-none}"
