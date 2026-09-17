# Moving Box Tracker

Track what went into which box during a house move, print QR labels on a Brother
QL-800, and scan them with a phone to find things again.

Answers two questions reliably:

- **What is in this box?** — scan the label, or search for an item by name.
- **Where is the thing I need right now?** — boxes report a `current_location`
  (truck, garage stack 3, storage unit) separately from the room they are
  destined for, because boxes sit in staging piles for days.

## Quick start

```bash
uv sync
uv run moving seed-rooms               # a starter set of rooms
uv run moving serve                    # http://localhost:8787
scripts/claude/smoke.sh                # prove it works end to end
```

The camera will not work over plain HTTP on a LAN address — see **HTTPS is
mandatory** below.

## HTTPS is mandatory

`getUserMedia` and `BarcodeDetector` are **secure-context only**. Served over
`http://192.168.x.x:8787` the camera silently fails: no scanning, no photo
capture, no PWA install prompt. `localhost` is exempt; a LAN IP is not.

Tailscale issues a real certificate, which solves this. To run it permanently:

```bash
scripts/claude/install-service.sh
```

That installs a launchd agent (starts at login, restarts if it dies) on
127.0.0.1:8787 and points `tailscale serve` at it. The app is then reachable at
**https://moving.example.ts.net**, which is also the base URL encoded
into every printed QR code. Remove it with `--uninstall`.

It uses `tailscale serve`, not `funnel`: reachable from your own tailnet
devices, never the public internet.

## Scanning

The stock phone camera reads a label and opens the box page — no app needed.
For scanning many boxes in a row, the app's own Scan view is faster.

`BarcodeDetector` (native, fast) is Chrome and Edge only, so on Firefox and
Safari the app falls back to jsQR, vendored locally so it works offline too.
Either way you can type a code by hand.

> **The base URL is baked into printed tape.** Changing the hostname later means
> reprinting labels. The in-app scanner is tolerant — it strips the host and
> reads the trailing code — so a change would break only the stock-camera
> tap-through, not lookup.

## Labels

62 mm continuous DK-2205, black only, 696 printable dots at 300 dpi.

Labels are **cut to their content**, between 25 mm and 90 mm. The tape is
continuous, so a fixed height would just print blank tape: a typical label comes
out around 50 mm and a bare code-and-QR one at 25 mm, which is roughly 44% less
tape per box across a whole move.

The destination room prints knocked out white on a solid black band. Mono tape
has no colour to sort by, and that band is what you actually read across a room
of stacked boxes.

During development the printer backend defaults to `fake`, which writes a PNG to
`var/labels/preview/` instead of burning tape. Tests force it.

```bash
uv run moving preview B-0001                    # PNG only, no printing
uv run moving print B-0001 --backend brother_ql # over USB
uv run moving print B-0001 --backend cups_raw   # if USB is claimed by CUPS
```

`cups_raw` needs `MOVING_PRINTER_QUEUE` set to the QL-800's queue name. It
refuses to run without one, rather than falling back to the system default
printer and firing a 40 KB raster at whatever laser printer is first in the list.

Turn **Editor Lite mode off** on the printer, or it presents as a mass-storage
device and ignores raster jobs.

## AI contents drafting

Photograph the open box before taping it; a local vision model drafts the item
list and summary, which you then edit. Requires Ollama:

```bash
ollama pull qwen3-vl:30b     # default, ~20 GB
ollama pull qwen3-vl:8b      # faster option
```

Drafts are **never** applied automatically. The suggestion appears with every
item ticked; untick what's wrong, edit the summary, then accept. Accepted items
are stored with `source='ai'` so AI-derived data stays distinguishable from
what you typed.

Measured on this machine: about 30 seconds the first time (the model has to
load), then **~6 seconds** per photo. If the model isn't installed the error
says so and gives you the `ollama pull` command, rather than blaming the
connection.

Photos are downscaled to 2048 px, their orientation baked in, and **all other
metadata stripped** — indoor photos carry GPS and this database gets exported.
Re-uploading the same shot is a no-op, so a retried upload can't duplicate it.

## Not built (deliberately)

Recorded so the reasoning is not lost, not because they were forgotten:

- **Pre-printed blank label batches** — print a strip of numbered labels, stick
  them on flat-packed boxes, fill contents in by scanning later.
- **Full offline-first write sync** — the PWA currently needs the tailnet
  reachable. Failed photo uploads retry from an IndexedDB queue, but edits do
  not queue.
- **DK-2251 black + red** — the QL-800 supports two-colour tape; the renderer is
  mono-only.
- Insurance valuation report, nested boxes, multi-user accounts.
- **A cloud vision provider.** Drafting is local-only. `vision.base.VisionProvider`
  is the seam if that ever changes.

## Backups

```bash
uv run moving backup        # verified, prunes to the last 14
```

The installer adds a nightly agent at 03:17. Backups use SQLite's online backup
API rather than a file copy — the service holds a connection open, and in WAL
mode `cp` can miss committed rows still in the `-wal` sidecar. Each backup is
verified *before* older ones are pruned, and is written as a single standalone
file you can open read-only or restore from a snapshot.

## Getting the data out

```bash
uv run moving export --format json -o moving.json
uv run moving export --format csv  -o moving.csv
uv run moving manifest                     # counts and weight per room
curl -O https://<host>/api/manifest.pdf    # for the movers
```

Exports name rooms rather than referencing ids, and nest items inside their
box, so they stand alone without the database.

The manifest's weight total states its own coverage ("weight covers 60 of 86
boxes"), because an unqualified total under a box count reads as the shipment
weight.

## Layout

| Path | Contents |
|---|---|
| `src/movingbox/` | FastAPI service, label rendering, vision providers |
| `web/` | PWA — no build step, plain ES modules |
| `migrations/` | Numbered SQL, applied against `PRAGMA user_version` |
| `scripts/claude/` | Operational scripts (backup, print test, model pull) |
| `docs/superpowers/specs/` | Design documents — never deleted |
| `var/` | Database, photos, label previews. Gitignored, backed up separately |
