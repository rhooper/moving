#!/bin/bash
# Rasterise an HTML page with headless Chrome. Usage: shot.sh in.html out.png W H
# Chrome writes the PNG and then sometimes never exits, so this waits for the
# file and then ends ONLY the instance it started (by its own PID) -- never
# pkill, other jobs on this machine run headless Chrome too.
set -u
S="$(cd "$(dirname "$0")" && pwd -P)"
in="$1"; out="$2"; w="$3"; h="$4"
rm -f "$out"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu \
  --user-data-dir="$S/chrome-profile" --virtual-time-budget=4000 --force-device-scale-factor=1 \
  --screenshot="$out" --window-size="$w,$h" --hide-scrollbars "file://$in" >/dev/null 2>&1 &
pid=$!
for i in $(seq 1 60); do
  if [ -s "$out" ]; then perl -e 'select(undef,undef,undef,0.7)'; break; fi
  if ! kill -0 $pid 2>/dev/null; then break; fi
  perl -e 'select(undef,undef,undef,0.5)'
done
kill $pid 2>/dev/null; wait $pid 2>/dev/null
ls -la "$out"
