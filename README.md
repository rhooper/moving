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

## Deploying

**Merging to `main` deploys.** The installer puts a `post-merge` git hook in
place, so a `git merge` or `git pull` that moves `main` in the main checkout
redeploys by itself. Nothing to remember; work cannot sit finished-but-not-
running, which is what used to happen.

```bash
scripts/claude/deploy.sh          # the same thing, by hand
scripts/claude/install-hooks.sh   # re-wire the hook (install-service.sh does this)
MOVING_NO_DEPLOY=1 git merge …    # merge without deploying, just this once
```

A deploy, in order:

1. **Refuses** unless the checkout is the main one (not a worktree), on `main`,
   with no modified tracked files, and served by the installed launchd agent.
2. **Backs up first** — `uv run moving backup`, before anything else. The merge
   has already put any new migration on disk and the old process re-reads that
   directory on every connection, so the live database can be migrated by the
   very next request. The backup goes in ahead of that, not after the tests.
3. `uv sync`, then **the whole test suite**. A red test stops the deploy dead
   and the running service is never touched — it keeps serving the old build.
4. Restarts the agent, waits for `/health`, and checks that the answer names
   the commit it just deployed. `/health` reports the revision the process
   started from, so "up" and "up on the new code" cannot be confused.

```console
$ scripts/claude/deploy.sh
Deployed.
  revision  c6fed8f  Merge feature-tape-counter
  previous  bfb2263d942a
  agent     ca.toybox.moving  pid 23971 -> 25527
  health    http://127.0.0.1:8787/health  ok, revision c6fed8f
  backup    var/backups/moving-20260917T205704-408189.db
```

Run it as often as you like — it is the same operation every time. Hooks live
in `.git/hooks`, which git does not version-control, so a fresh clone has none
until `install-hooks.sh` (or `install-service.sh`) runs.

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

Labels run **along** the tape: a fixed 4 inches (1200 x 696 px), with the box's
identity on the left and its itemised contents listed on the right. A shelf of
same-size labels is much easier to read along than a row of ragged ones, and at
that size there is room for the list that lets you pick the right box without
opening it. A box with no itemised items gives the identity the whole width
instead of printing an empty column.

The design is laid out readably and turned a quarter turn at print time
(`printer.to_raster`), so a preview is never shown sideways. Verified on tape.

The destination room prints knocked out white on a solid black band. Mono tape
has no colour to sort by, and that band is what you actually read across a room
of stacked boxes.

The older cut-to-content portrait form is still there —
`MOVING_LABEL_ORIENTATION=portrait`, or `--orientation portrait` — sizing
between 25 mm and 90 mm.

**A label will not print for a box with nothing recorded in it.** That is the
expensive mistake: the tape is spent, it goes on the box, and the box is then
indistinguishable from an unlabelled one until you open it. Add a summary or
some items, or tick *print anyway*. As with an unknown code, one empty box
rejects the whole batch. Previewing is never gated — looking costs nothing.

During development the printer backend defaults to `fake`, which writes a PNG to
`var/labels/preview/` instead of burning tape. Tests force it.

```bash
uv run moving preview B-0001                    # PNG only, no printing
uv run moving print B-0001 --backend brother_ql # over USB
uv run moving print B-0001 --backend cups_raw   # if USB is claimed by CUPS
uv run moving print B-0001 --orientation portrait
```

The CLI is deliberately *not* gated on contents — it is the escape hatch.

`cups_raw` needs `MOVING_PRINTER_QUEUE` set to the QL-800's queue name. It
refuses to run without one, rather than falling back to the system default
printer and firing a 40 KB raster at whatever laser printer is first in the list.

Turn **Editor Lite mode off** on the printer, or it presents as a mass-storage
device and ignores raster jobs.

## Photos that list the contents for you

Photograph the open box before taping it. A vision model reads each photo in
the background and adds what it sees to the contents list, marked
*autogenerated*; you carry on packing while it works.

**Claude reads the photo; Ollama is there when it cannot.** `claude-sonnet-5`
does the background read and `claude-opus-5` the closer look. If the API cannot
be reached — no internet, no key, rate limited, or the budget is spent — the
local model reads it instead, and the app says so rather than failing. That
matters on moving day, when the Mac is unplugged and in a van: the tailnet
survives things the internet does not.

```bash
cp .env.example .env               # then put ANTHROPIC_API_KEY in it
chmod 600 .env

ollama pull qwen3-vl:4b-instruct   # the fallback: 3.3 GB
ollama pull qwen3-vl:8b-instruct   # the fallback's closer look: 6.1 GB
```

With no key at all the app still works, entirely locally. That is a supported
setup, not a broken one.

**What it costs, and the cap.** About **three quarters of a cent a photo**
($0.75 per 100) for the background read, and about **two cents** ($1.88 per
100) for a closer look — so a move of 300–1,000 photos runs a few dollars.
Settings → **Reading photos** shows the running total against a cap
(`MOVING_VISION_BUDGET_USD`, $30 by default) and says which model is reading
your photos right now. Past the cap nothing more is spent: photos are read
locally instead and the list keeps filling itself in.

Tap a photo to see what was read from it, and which model read it; **Look
closer** there runs the slower, more careful one, which is better at
handwriting and brand names. If you change the local models, mind the
`-instruct`: the plain `qwen3-vl` tags are "thinking" models that take several
times as long for no better result.

What it will and will not touch:

- Items it adds are marked *autogenerated* (`source='ai'`). A second photo of the
  same things adds nothing; a better count raises a quantity, never lowers one.
- Anything **you** typed is left alone: your items are never renamed, re-counted
  or removed, and a summary you wrote is never overwritten. It only fills a
  summary that is empty or that it wrote itself.
- Tap an autogenerated name to correct it. Once you have, it is yours, and the
  model leaves it alone.

Each photo shows a countdown while it is being read, then how many items were
found. The countdown is an estimate from recent runs; the first photo after a
quiet spell is slower, because the model has to load.

The one-line summary can also be assembled from the items with no model at all
("3 baking pans, kettle"). It is offered the same way — proposed, never
applied. Plain assembly is instant, identical every time, and works offline;
for a list you have already typed, a model would add latency without adding
much.

Why the cloud tier at all: measured over the real database, the local
`qwen3-vl:4b-instruct` averaged **19.6 s** a photo with a **92 s** worst case,
and **5 of 47 jobs failed outright**. Claude answers in a few seconds for less
than a cent. If the local model is the one running and isn't installed, the
error says so and gives you the `ollama pull` command rather than blaming the
connection.

Photos are downscaled to 2048 px, their orientation baked in, and **all other
metadata stripped** — indoor photos carry GPS and this database gets exported.
Re-uploading the same shot is a no-op, so a retried upload can't duplicate it.

## Decided against

Not a backlog. These were considered and ruled out, recorded so nobody
re-proposes them as oversights. (**"A cloud vision provider" was on this list
and came off it** in September 2026: the local model was measured at 19.6 s a
photo with a 1-in-9 failure rate, against under a cent a photo for Claude. It
is a hybrid now, and the local model is the fallback — see *Photos that list
the contents for you*.)

- **Pre-printed blank label batches.** Create a box and print its label one at
  a time instead; that already gives you label-first packing without a
  strip-of-blanks flow to manage.
- **Offline write sync.** The PWA needs the tailnet reachable. Failed photo
  uploads retry from a queue, but edits do not, and are not going to.
- **DK-2251 black + red.** The QL-800 can do two-colour tape; the renderer is
  mono only. The room name is knocked out white on a solid black band, which is
  what does the sorting work on mono stock.
- Insurance valuation report, nested boxes, multi-user accounts.

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
| `scripts/claude/` | Operational scripts (install, deploy, backup, print test) |
| `scripts/claude/hooks/` | The git hooks themselves; `install-hooks.sh` wires them up |
| `docs/superpowers/specs/` | Design documents — never deleted |
| `var/` | Database, photos, label previews. Gitignored, backed up separately |
