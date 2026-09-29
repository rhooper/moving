#!/bin/bash
# Purpose: every browser check, against a throwaway server on a spare port with
#          its own empty database, the fake printer and the stub vision
#          provider -- the checks that write must never touch the live service.
#          It serves the checkout it is run from, so a worktree is fine.
# Date:    2026-09-18
# Usage:   scripts/claude/browser_checks.sh          (or: make browser-check)
#          PORT and CDP_PORT keep parallel runs apart. Exit status is non-zero
#          if any check failed. ~2 minutes.
#
# Headless Chrome traps the checks work around: each Chrome needs its own
# debugging port and --user-data-dir, and is stopped by its own pid -- never
# `pkill` Chrome by pattern, since other runs may be going in parallel.
# --screenshot with a debugging port never exits. A narrow --window-size does
# not narrow the layout (use Emulation.setDeviceMetricsOverride). A headless
# page fires no blur without Emulation.setFocusEmulationEnabled.
set -u
unset CDPATH   # may be set in the caller's shell, and it corrupts $(cd ... && pwd)

HERE=$(cd "$(dirname "$0")/../.." && pwd -P)
cd "$HERE" || exit 1
PORT=${PORT:-8797}
T=$(mktemp -d)

# Inline on the environment of *this* script only: nothing here can reach the
# real var/ directory, whichever checkout it is run from.
export MOVING_DB_PATH=$T/t.db MOVING_PHOTO_DIR=$T/photos MOVING_LABEL_PREVIEW_DIR=$T/prev
export MOVING_BACKUP_DIR=$T/bk MOVING_PRINTER_BACKEND=fake
export MOVING_VISION_PROVIDER=stub MOVING_VISION_STUB_SECONDS=3
# A throwaway server has no deploy behind it, so it would serve every asset
# unversioned, unlike production. Name a revision so the checks load it the
# production way.
export MOVING_REVISION="check-$$"

# A brand-new database must be migrated before a browser touches it: a page
# load fires requests in parallel and they would race to create the schema.
uv run moving seed-rooms >/dev/null 2>&1
uv run moving serve --port "$PORT" >"$T/server.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null; rm -rf "$T"' EXIT
for _ in $(seq 1 80); do curl -s -o /dev/null "http://127.0.0.1:$PORT/health" && break; sleep 0.25; done

B=http://127.0.0.1:$PORT
post() { curl -s -X POST -H 'content-type: application/json' -d "$2" "$B$1"; }
code() { python3 -c 'import sys,json;print(json.load(sys.stdin)["code"])'; }
ROOM=$(curl -s "$B/api/rooms" | python3 -c 'import sys,json; print([r["id"] for r in json.load(sys.stdin) if r["kind"]!="source"][0])')
ONE=$(post /api/boxes "{\"content_summary\":\"pots and pans\",\"destination_room_id\":$ROOM}" | code)
TWO=$(post /api/boxes "{\"content_summary\":\"books\",\"destination_room_id\":$ROOM}" | code)
VIEW=$(post /api/boxes "{}" | code)
post "/api/boxes/$ONE/items" '{"name":"stock pot"}' >/dev/null
# Things inside things, for the read-only checks to find: a crate holding a
# bag, a small box (with two bags of its own) and a lamp. The search word
# "samovar" is inside the small box, two levels down.
CRATE=$(post /api/boxes "{\"kind\":\"crate\",\"content_summary\":\"kitchen\",\"destination_room_id\":$ROOM}" | code)
post /api/boxes "{\"kind\":\"bag\",\"content_summary\":\"cutlery\",\"parent_code\":\"$CRATE\"}" >/dev/null
TIN=$(post /api/boxes "{\"kind\":\"box\",\"content_summary\":\"tea and the samovar\",\"size\":\"small\",\"parent_code\":\"$CRATE\"}" | code)
post /api/boxes "{\"kind\":\"item\",\"content_summary\":\"Desk lamp\",\"parent_code\":\"$CRATE\"}" >/dev/null
post /api/boxes "{\"kind\":\"bag\",\"content_summary\":\"teaspoons\",\"parent_code\":\"$TIN\"}" >/dev/null
post /api/boxes "{\"kind\":\"bag\",\"content_summary\":\"strainer\",\"parent_code\":\"$TIN\"}" >/dev/null
uv run python -c "
from PIL import Image, ImageDraw
im = Image.new('RGB', (1600, 1200), (190, 160, 120))
ImageDraw.Draw(im).rectangle([300, 250, 1300, 950], fill=(120, 85, 50))
im.save('$T/p.jpg', quality=88)"
curl -s -F "file=@$T/p.jpg" "$B/api/boxes/$ONE/photos" >/dev/null
sleep 5   # let that photo's stub job finish, so the read-only checks see a steady page

FAIL=0
run() {
  local name=$1; shift
  "$@" >"$T/out.txt" 2>&1
  local rc=$?
  printf '%-16s exit=%s  %s\n' "$name" "$rc" "$(grep -E '^all [0-9]+ passed|[0-9]+ failed$' "$T/out.txt" | tail -1)"
  if [ $rc -ne 0 ]; then grep -E '^FAIL|harness|refus|Error' "$T/out.txt" | head -12; FAIL=1; fi
}
run ui_check       node scripts/claude/ui_check.mjs "$B" "$ONE"
run wedge_check    node scripts/claude/wedge_check.mjs "$B" "$ONE" "$TWO"
run viewer_check   node scripts/claude/viewer_check.mjs "$B" "$VIEW" "$T/p.jpg" "$T"
run copies_check   node scripts/claude/copies_check.mjs "$B"
run autosave_check node scripts/claude/autosave_check.mjs "$B"
run nesting_check  node scripts/claude/nesting_check.mjs "$B"
run newbox_check   node scripts/claude/newbox_check.mjs "$B"
# Its own two servers on its own ports: it ships a second revision mid-run,
# which the shared server cannot.
run strip_cache    env PORT=$((PORT + 1)) CDP_PORT=$((CDP_PORT + 1)) node scripts/claude/strip_cache_check.mjs
# Its own server, on a free port, restarted under new revisions: a deploy as
# an open page sees one. It never touches the one above.
run reload_check   node scripts/claude/reload_check.mjs

TRACEBACKS=$(grep -a -c Traceback "$T/server.log")
echo "server tracebacks: $TRACEBACKS"
[ "$TRACEBACKS" -eq 0 ] || FAIL=1
exit $FAIL
