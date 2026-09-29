#!/usr/bin/env bash
# Purpose: bump the minor or major version by hand, and stage it. The patch
#          bumps itself: the pre-commit hook adds one on every commit, and
#          leaves a commit alone when version.py is already staged -- which is
#          what this does, so `make version-minor && git commit` gives x.Y+1.0
#          exactly, not x.Y+1.1.
# Date:    2026-09-20
# Usage:   scripts/claude/bump.sh minor|major
set -euo pipefail
CDPATH=""   # may be set in the caller's shell; a relative cd through it prints its target

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
FILE="src/movingbox/version.py"
current="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$FILE")"
major="${current%%.*}"; rest="${current#*.}"; minor="${rest%%.*}"

case "${1:-}" in
  minor) next="$major.$((minor + 1)).0" ;;
  major) next="$((major + 1)).0.0" ;;
  *) echo "usage: $0 minor|major" >&2; exit 2 ;;
esac

# -i.bak, not -i '': the one spelling BSD and GNU sed both accept.
sed -i.bak "s/^__version__ = \"$current\"$/__version__ = \"$next\"/" "$FILE"
rm -f "$FILE.bak"
git add "$FILE"
echo "$current -> $next (staged; commit to keep it)"
