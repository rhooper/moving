#!/usr/bin/env bash
# Purpose: install this repo's git hooks, which git does not version-control;
#          without them merges to main silently stop deploying.
#          install-service.sh calls this. What lands in .git/hooks is a shim
#          that execs the version-controlled scripts/claude/hooks/<name>, so
#          editing a hook needs no reinstall.
#
# Usage:   scripts/claude/install-hooks.sh [--uninstall]
set -euo pipefail

# CDPATH may be set in the caller's shell, and a relative `cd` through it prints its
# target into the $( ) below, giving a path with a newline in it.
CDPATH=""

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SOURCE_DIR="$REPO/scripts/claude/hooks"
MARKER="installed by scripts/claude/install-hooks.sh"

cd "$REPO"

# Hooks live in the *common* git dir, shared by every worktree -- so this
# installs to .git/hooks even when run from under .claude/worktrees.
if HOOKS_PATH="$(git config --get core.hooksPath 2>/dev/null)" && [[ -n "$HOOKS_PATH" ]]; then
  [[ "$HOOKS_PATH" == /* ]] || HOOKS_PATH="$REPO/$HOOKS_PATH"
  HOOKS_DIR="$HOOKS_PATH"
else
  HOOKS_DIR="$(cd "$(git rev-parse --git-common-dir)" && pwd -P)/hooks"
fi

HOOKS=()
for path in "$SOURCE_DIR"/*; do
  # An `x && y` list here would be the loop body's last command, and a
  # non-file entry would take the whole script out under `set -e`.
  if [[ -f "$path" ]]; then HOOKS+=("$(basename "$path")"); fi
done
(( ${#HOOKS[@]} )) || { echo "No hooks in $SOURCE_DIR" >&2; exit 1; }

if [[ "${1:-}" == "--uninstall" ]]; then
  for name in "${HOOKS[@]}"; do
    target="$HOOKS_DIR/$name"
    if [[ -f "$target" ]] && grep -q "$MARKER" "$target" 2>/dev/null; then
      rm -f "$target"
      echo "Removed $target"
    fi
  done
  echo "Merges to main will no longer redeploy. scripts/claude/deploy.sh still works by hand."
  exit 0
fi

mkdir -p "$HOOKS_DIR"

for name in "${HOOKS[@]}"; do
  target="$HOOKS_DIR/$name"

  # Never clobber a hook somebody else put there.
  if [[ -e "$target" ]] && ! grep -q "$MARKER" "$target" 2>/dev/null; then
    kept="$target.replaced-$(date +%Y%m%dT%H%M%S)"
    mv "$target" "$kept"
    echo "  kept the existing $name as $(basename "$kept")"
  fi

  # The shim resolves the checkout the hook fires for, not the one this ran
  # in: the hooks directory is shared, and a worktree must run (and be refused
  # by) its own copy.
  cat > "$target" <<SHIM
#!/usr/bin/env bash
# $MARKER -- do not edit.
# The hook itself is version-controlled at scripts/claude/hooks/$name.
hook="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)/scripts/claude/hooks/$name"
[ -x "\$hook" ] || exit 0
exec "\$hook" "\$@"
SHIM
  chmod +x "$target"
  chmod +x "$SOURCE_DIR/$name"
  echo "Installed $target -> scripts/claude/hooks/$name"
done

echo
echo "A merge that moves main in the main checkout now runs scripts/claude/deploy.sh."
echo "Merges in a worktree, or onto any other branch, are ignored."
echo "MOVING_NO_DEPLOY=1 skips it for one command."
