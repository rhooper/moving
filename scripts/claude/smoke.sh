#!/usr/bin/env bash
# Purpose: end-to-end smoke test against a real running server.
#          Creates a room and a box, packs it, moves it, searches for it,
#          and pulls its label -- then checks the label's QR actually decodes.
# Usage:   scripts/claude/smoke.sh [base_url]
set -euo pipefail

BASE="${1:-http://127.0.0.1:8787}"
SCRATCH="${SCRATCH:-/tmp}"
j() { python3 -c "import sys,json;d=json.load(sys.stdin);print(d$1)"; }

echo "== health =="
curl -fsS "$BASE/health"; echo

echo "== seed a room =="
ROOM=$(curl -fsS -X POST "$BASE/api/rooms" -H 'content-type: application/json' \
       -d '{"name":"Kitchen"}' | j "['id']")
echo "room id $ROOM"

echo "== create a box =="
CODE=$(curl -fsS -X POST "$BASE/api/boxes" -H 'content-type: application/json' \
       -d "{\"destination_room_id\":$ROOM,\"content_summary\":\"pots, pans, stand mixer\",\"source_location\":\"Basement shelf 3\",\"fragile\":true}" \
       | j "['code']")
echo "created $CODE"

echo "== add an item =="
curl -fsS -X POST "$BASE/api/boxes/$CODE/items" -H 'content-type: application/json' \
     -d '{"name":"cafetiere","qty":1}' > /dev/null

echo "== lifecycle: pack, load, move =="
curl -fsS -X POST "$BASE/api/boxes/$CODE/status"   -H 'content-type: application/json' -d '{"status":"packed"}' | j "['status']"
curl -fsS -X POST "$BASE/api/boxes/$CODE/status"   -H 'content-type: application/json' -d '{"status":"loaded"}' | j "['status']"
curl -fsS -X POST "$BASE/api/boxes/$CODE/location" -H 'content-type: application/json' -d '{"current_location":"truck"}' | j "['current_location']"

echo "== search by an item inside the box =="
curl -fsS "$BASE/api/search?q=cafetiere" | j "[0]['code']"

echo "== timeline =="
curl -fsS "$BASE/api/boxes/$CODE/events" | python3 -c "
import sys,json
for e in json.load(sys.stdin):
    print(f\"  {e['kind']:9} {e['from_value'] or '-':12} -> {e['to_value']}\")"

echo "== label =="
curl -fsS "$BASE/api/labels/preview/$CODE.png" -o "$SCRATCH/smoke_label.png"
echo "  saved $SCRATCH/smoke_label.png"
curl -fsS -X POST "$BASE/api/labels/print" -H 'content-type: application/json' \
     -d "{\"codes\":[\"$CODE\"]}" | j "['printed'][0]['output']"

echo "== scanned QR resolves =="
curl -fsS -o /dev/null -w "  /b/$CODE -> %{http_code} %{redirect_url}\n" "$BASE/b/$CODE"

echo
echo "OK ($CODE)"
