# CLAUDE.md — Moving Box Tracker

Working notes for Claude sessions. See `README.md` for user-facing docs and
`docs/superpowers/specs/` for design rationale.

## Commands

```bash
uv sync                          # install deps (Python 3.13)
uv run moving serve              # dev server on :8787
uv run moving seed-rooms         # starter set of rooms
uv run moving preview B-0001     # render a label to PNG, no printing
uv run moving print  B-0001      # honours MOVING_PRINTER_BACKEND (default: fake)
uv run moving reindex            # rebuild the FTS index
uv run moving backup             # verified backup + prune
uv run moving export --format csv -o out.csv
uv run moving manifest           # box counts per room
scripts/claude/try_vision.py     # call the real model (slow, non-deterministic)
uv run pytest                    # printer forced to `fake` in conftest
scripts/claude/install-service.sh          # launchd + tailscale serve (persistent https)
scripts/claude/install-service.sh --uninstall
scripts/claude/deploy.sh         # backup, test, restart, verify — normally automatic
scripts/claude/install-hooks.sh  # wire up the post-merge hook (install-service.sh calls it)
uv run ruff check src tests scripts
scripts/claude/smoke.sh          # end-to-end against a running server
scripts/claude/render_samples.py # contact sheet of sample labels to eyeball
tailscale serve --bg 8787        # HTTPS, required for camera access
```

## Hard-won facts

**HTTPS is not optional for the PWA.** `getUserMedia` and `BarcodeDetector` are
secure-context only. On a LAN IP they fail *silently* — no error, just no
camera. `localhost` is exempt. `tailscale serve` provisions a real cert for
`moving.example.ts.net`, which is also the QR base URL.

Consequences that are easy to get wrong:
- **The `/b/{code}` redirect must stay relative.** Tailscale terminates TLS and
  proxies plain HTTP, so an absolute redirect rebuilt from the request says
  `http://` and drops the phone out of a secure context. Two tests guard this.
- **uvicorn runs with `proxy_headers`, trusting loopback only**, so anything
  derived from `request.url` reports https.
- **`serve` binds 127.0.0.1**, not 0.0.0.0 — `tailscale serve` proxies to
  loopback, and binding everything would add an unauthenticated LAN listener.
- `tailscale serve`, never `tailscale funnel`: tailnet devices only.

**`BarcodeDetector` is Chrome/Edge only — the phone here runs Firefox.** So
`web/scan.js` falls back to jsQR, vendored at `web/jsQR.js` (MIT) rather than
loaded from a CDN, so scanning also works offline. Manual code entry is always
present as the last resort.

**Label geometry, read from `brother_ql.labels` rather than assumed:**
label id `62`, `FormFactor.ENDLESS`, `dots_total=(732, 0)`,
`dots_printable=(696, 0)`. Render at **696 px wide**.

**Labels are landscape by default: a fixed 1200 x 696 px** (4 in x 62 mm at
300 dpi), identity left, itemised contents right. Fixed length on purpose — a
shelf of same-size labels reads far better than ragged ones, and the size buys
room for the contents list. With no items the identity takes the full width
rather than printing an empty `CONTENTS` heading.

**Rotation belongs to the printer, not the layout.** `layout.render()` returns
an image that reads normally; `printer.to_raster()` turns a landscape design a
quarter turn so its 696 dots land across the tape. `build_instructions` rotates
*before* its width check — otherwise a correctly sized landscape label is
rejected for being 1200 px wide. The rotation direction was settled on tape,
not in software; it is correct as written.

`orientation="portrait"` is the older cut-to-content form, still supported and
still tested, sizing between `MIN_HEIGHT` (300) and `DEFAULT_HEIGHT` (1063).
`MOVING_LABEL_ORIENTATION` selects the default.

**Printing refuses a box with no recorded contents** (`allow_empty` off by
default, 409). Previewing is not gated, and neither is the CLI — that is the
escape hatch. One empty box rejects the whole batch, as an unknown code does.

**`brother_ql` is stale, in two specific ways:**
1. It warns `brother_ql.devicedependent is deprecated` on import (suppressed at
   the one import site in `labels/printer.py`).
2. Its rescale path calls `PIL.Image.ANTIALIAS`, **removed in Pillow 10**. Hand
   it anything other than a 696 px-wide image and it dies with an
   `AttributeError` that says nothing about the real problem. `build_instructions`
   checks the width first and raises a useful error.

   If it breaks further, the drop-in is the GPL-3.0 fork
   `luxardolabs/brother_ql`. Everything goes through the `PrinterBackend`
   protocol so that swap touches one file.

**`cv2.QRCodeDetector` cannot be trusted** — it failed to decode a valid
version-3 QR that was pixel-identical to segno's own reference render. Tests use
**zbar** (`brew install zbar`), the engine real scanners are built on. pyzbar
finds libzbar via `ctypes.util.find_library`, which does not search Homebrew's
prefix; `tests/conftest.py` sets `DYLD_FALLBACK_LIBRARY_PATH` in `os.environ`
before the import. That works where `DYLD_LIBRARY_PATH` would not, because
ctypes reads it at call time whereas dyld caches its own at exec.

**Fonts are bundled** (`labels/fonts/Inter.ttf`, OFL). Golden-image tests
compare rendered bytes, so a system font update would break them spuriously.

**Vision drafting is local-only by deliberate choice.** `qwen3-vl:30b` through
Ollama. Measured on this machine: ~30 s on the first call (loading ~20 GB) and
**~6 s warm**, ~12 s end-to-end through the API — fast enough to answer
synchronously, so there is no job queue. A cloud provider would slot in behind
`vision.base.VisionProvider`; none is built, because local was the choice.

**`base.parse` is deliberately forgiving of the reply, strict about the
outcome.** Local models wrap JSON in markdown fences, prepend "Sure! Here
is…", and return quantities like `"lots"` however firmly the prompt forbids
it — all of that is mined and coerced. But a reply with no usable JSON raises
`DraftUnreadable` rather than returning an empty draft, which would read as
"the model saw an empty box".

**FastAPI's `include_router` does not flatten into `app.routes`** in this
version: each included router is one `_IncludedRouter` wrapper whose real
routes hang off `original_router`. Its `routes` attribute is a *string* — walk
that and you iterate its characters and silently inspect nothing. This
initially made `test_api_structure.py` pass while testing almost no routes, so
that file now has a test guarding its own route walk.

## Deployment is automatic, and the details matter

**Merging to `main` in the main checkout redeploys.** `scripts/claude/hooks/post-merge`
is the hook; `install-hooks.sh` drops a shim into `.git/hooks/post-merge` that
execs it, and `install-service.sh` runs that installer. Before this existed,
several rounds of finished work sat merged and undeployed while the features
were reported missing.

- **Never deploy from a worktree.** `.git/hooks` lives in the *common* git dir,
  so the hook fires for every worktree in the repo. Both the hook and
  `deploy.sh` compare `--absolute-git-dir` against `--git-common-dir` and bail
  when they differ. A worktree is also the wrong tree: the agent serves the
  main checkout, and worktrees get deleted.
- **The backup runs first, before the tests, not after them.** `db.migrate()`
  re-globs the migrations directory on *every* `db.connect()`, and the service
  opens a connection per request — so the moment a merge lands a new migration
  file on disk, the still-running old process will apply it to the live
  database on its next request. The backup has to be in front of that window,
  and the window is not closeable from a deploy script.
- **`/health` reports `revision`**, read **once at startup** from
  `var/deployed-revision` (written by `deploy.sh` just before the restart).
  Reading it per request would report the new commit the instant the file was
  written, whether or not the restart happened — which is precisely the failure
  the whole thing exists to catch. `deploy.sh` treats a mismatch as a failure.
- **Tests are the gate.** A red suite aborts before anything touches launchd,
  so the old build keeps serving. `--if-changed` (what the hook passes) is a
  silent no-op only when the recorded revision matches *and* `/health` answers;
  a service that is down gets redeployed even if the commit has not moved.
- `MOVING_NO_DEPLOY=1` skips the hook. `MOVING_SERVICE_LABEL` /
  `MOVING_SERVICE_PORT` override the agent label and port in both
  `install-service.sh` and `deploy.sh` — that is how the deploy path gets
  exercised against a throwaway agent without touching the live one.

**`CDPATH` is set in this user's shell, and it corrupts `$(cd … && pwd)`.**
When `cd` resolves a *relative* path through `CDPATH`, bash prints the
destination to stdout — so `$(cd "$(git rev-parse --git-common-dir)" && pwd -P)`
comes back as *two* lines, and every path comparison built on it silently
fails. It first showed up as the worktree guard refusing the main checkout, and
as `install-hooks.sh` creating a directory with a newline in its name. Every
script under `scripts/claude/` clears `CDPATH` before doing anything.

**`launchctl print` exits non-zero for a label that is not loaded**, which under
`set -euo pipefail` killed `deploy.sh` mid-restart — at exactly the moment the
service was down. `agent_pid` in `scripts/claude/lib/launchd.sh` returns 0 with
empty output instead. Same family of trap: `x && y` as a statement is the last
command in its context, so `set -e` exits on it when `x` is false; use `if`.

**`reload_agent` lives in `scripts/claude/lib/launchd.sh`**, sourced by both
`install-service.sh` and `deploy.sh`. There is one copy on purpose — `launchctl
bootout` is asynchronous, and bootstrapping the same label too soon fails with
"Bootstrap failed: 5: Input/output error" *after* having already unloaded the
service, leaving nothing listening.

**The plist heredoc in `install-service.sh` is unquoted**, so backticks in its
XML comments run as commands. That silently executed `fake` and dropped the
word from the generated comment; they are escaped now.

## Conventions

- **Printer backend defaults to `fake`** everywhere. Tests force it in
  `tests/conftest.py`, not via pyproject's `env =` key — pytest ignores that key
  unless `pytest-env` is installed, and does so silently.
- **Warnings are errors** (`filterwarnings = ["error"]`). This caught sqlite
  connections leaking out of tests. Third-party deprecations get individual
  `ignore` entries rather than relaxing the default.
- **Migrations are numbered SQL** in `src/movingbox/migrations/` (inside the
  package, so they survive installation), applied against `PRAGMA user_version`.
  Never edit an applied migration — add a new one.
- **FTS is maintained explicitly**: call `search.reindex_box(conn, box_id)`
  after any mutation touching a box, its items or its photos. Chosen over
  triggers because the indexed text spans four tables and a function is
  testable. All user input goes through `_match_expression`, which quotes every
  word — FTS5 treats `-` as an operator, so an unescaped scan of `B-0042` either
  raises or matches nothing.
- **Status and location are not in `EDITABLE`** and cannot be set via
  `update_box`. They have dedicated calls so every transition reaches the event
  log. `schemas.Strict` forbids unknown fields, so a PATCH carrying `status`
  fails 422 rather than being silently dropped.
- **Summaries can be assembled from items without a model**
  (`summarise.from_items`). Offered as a suggestion, never applied — the same
  rule as a photo draft. Plain assembly because the items have already been
  typed or reviewed: it should be instant, identical every time, and offline.
- **AI output is provenance-tagged**: items from a vision draft get
  `source='ai'`. **Never auto-apply a draft** — `ai.draft_for_box` returns a
  proposal and writes nothing to the box; the PWA's review panel applies it
  through the ordinary items/PATCH endpoints once a person accepts.
- **Every route taking `get_conn` must be a sync `def`.** `get_conn` is a sync
  generator dependency so FastAPI runs it in a threadpool, while an `async def`
  endpoint runs on the event loop — the sqlite handle then crosses threads and
  sqlite3 refuses it outright. Photo upload hit this for real.
  `tests/test_api_structure.py` enforces it.
- **Photos are normalised on the way in**: downscaled to 2048 px, EXIF
  orientation baked in and all other metadata stripped (indoor photos carry
  GPS, and this database gets exported), deduplicated by sha256 so the phone's
  upload retries are harmless.
- Scripts live in `scripts/claude/` with a purpose header.
- Ruff's `B008` is disabled for FastAPI's `Depends`/`Query`/`Header` defaults
  via `extend-immutable-calls` — it is a false positive for that idiom.

## Domain model gotcha

`destination_room_id` and `current_location` are **different things** and both
matter. A box destined for the kitchen may currently be on the truck.
Conflating them breaks the main use case ("where is the coffee maker *right
now*?").

## Where the real database lives

`var/moving.db` **in the main checkout**, not in a worktree. `config.ROOT` is
derived from the package location, so running the CLI from a worktree silently
uses that worktree's `var/` — and worktrees get deleted. B-0001 has a physical
label in circulation; do not lose its row. Back up before anything destructive.

## Status

Phases 0–4 complete: schema, store, REST API, search, label renderer, printer
backends, CLI. 90 tests passing, verified end to end against a running server
via `scripts/claude/smoke.sh`.

**Phase 4 physical checkpoint PASSED (2026-09-17).** A real label for B-0001
printed on DK-2205 over the `brother_ql` USB backend, and scanning its QR with a
phone opened the box page over Tailscale HTTPS. The layout is therefore
committed: changing it now means reprinting anything already stuck to a box.

Hardware notes from that run: the QL-800 appears on USB as `0x04f9:0x209b` but
registers **no CUPS queue**, so `brother_ql` (pyusb) is the working backend and
`cups_raw` is not currently usable. MagicDNS does not resolve from the sandboxed
tool shell — test with
`curl --resolve moving.example.ts.net:443:$(tailscale ip -4)` — but
resolves fine from a phone.

Phases 5, 6 and the deploy half of 9 are also done: the PWA shell (box detail,
status track, location, items, create-and-print), the camera scanner, and a
launchd agent behind `tailscale serve` so `https://moving.example.ts.net`
stays up across reboots. 99 tests passing.

Design note: the PWA deliberately mirrors the printed label — Inter (served from
the package, not duplicated), the code set huge as the hero, room in the same
black knockout band, true black rather than a tinted near-black. The point is
that after scanning a physical object the screen confirms it is the same one.

**Every phase of the plan is built.** Schema, store, REST API, search, label
rendering, three printer backends, CLI, PWA with scanner, exports, manifest,
verified nightly backups, photo storage, and AI drafting. Running under launchd
behind `tailscale serve`.

**Closed decisions — do not re-propose these as gaps** (rationale in README
§ Decided against): no pre-printed blank label batches, no offline write sync,
no DK-2251 two-colour printing, no cloud vision provider.

Known limitations that are real, not decisions:

- `cups_raw` is unusable here because the QL-800 registers no CUPS queue. USB
  works; this only matters if macOS ever claims the device.
- The phone UI for photos and drafting is verified by API and syntax check, but
  has not been exercised on a real handset.

The original build order and phase gates are kept for history in
`~/.claude/plans/create-a-packing-tracking-atomic-tarjan.md`.
