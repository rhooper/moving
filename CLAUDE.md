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

**Labels are cut to content, not to a fixed 90 mm.** The tape is continuous, so
`render()` sizes between `MIN_HEIGHT` (300) and `DEFAULT_HEIGHT` (1063, the
cap). A typical label is ~590 px and a sparse one 300 px — roughly 44% less tape
per box than a fixed height. Pass `height=` for an exact cut.

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

**A live refresh is an interruption, and the PWA treats it as one.** The list
updates rows *in place* keyed by box code (`reconcile` in `web/live.js`):
rebuilding the markup would destroy every `<a>`, so a tap that began on one
lands on whatever node replaced it and you open the wrong box. On top of that
a refresh is held back entirely while a field is focused or dirty, while an
unaccepted AI draft is on screen, while a pointer is down, and for 600 ms
after — the click has not landed when the finger lifts. Held refreshes raise
the `#live` banner instead, and the new-box form and scanner are never
refreshed at all (`affects()` matches no view name for them).

**The websocket key travels in the query string** because a browser's
`WebSocket` constructor takes a URL and nothing else — `X-API-Key` cannot be
attached to the handshake. That does put the key in uvicorn's access log
(`"WebSocket /api/events?key=..." [accepted]`), which is acceptable only
because this runs on a tailnet and usually has no key set at all.

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
- **AI output is provenance-tagged**: items from a vision draft get
  `source='ai'`. **Never auto-apply a draft** — `ai.draft_for_box` returns a
  proposal and writes nothing to the box; the PWA's review panel applies it
  through the ordinary items/PATCH endpoints once a person accepts.
- **Every route taking `get_conn` must be a sync `def`.** `get_conn` is a sync
  generator dependency so FastAPI runs it in a threadpool, while an `async def`
  endpoint runs on the event loop — the sqlite handle then crosses threads and
  sqlite3 refuses it outright. Photo upload hit this for real.
  `tests/test_api_structure.py` enforces it.
- **The change socket (`/api/events`) must stay database-free.** A websocket
  endpoint has no choice about being `async def`, so the rule above cannot be
  satisfied by making it sync — it has to need no connection at all. That is
  why the channel carries only an event kind and a box code and clients
  refetch over REST; `test_no_websocket_takes_a_database_connection` guards it.
  Publishing crosses the same boundary in the other direction: routes publish
  from the threadpool, the queue lives on the loop, and `asyncio.Queue` is not
  thread-safe — hence `loop.call_soon_threadsafe` in `events._Subscriber`.
- **Mutations are announced by the route, not by `store`.** `store` is shared
  with the CLI, which runs in another process and could not reach a connected
  phone anyway. So a CLI change is deliberately silent; the phone finds out on
  its next refresh.
- **Writes carry `X-Client-Id`, and the event carries it back as `origin`.**
  The device that made a change has already redrawn from the response; without
  the tag it redraws again on its own echo, which is how half-typed text
  disappears. `web/live.js` `affects()` drops events whose origin is itself.
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

Live updates were added after that: `/api/events` broadcasts which box changed,
and `web/live.js` reconnects with jittered backoff, falls back to polling, and
decides when a refresh is safe. 251 tests passing.

Known limitations that are real, not decisions:

- `cups_raw` is unusable here because the QL-800 registers no CUPS queue. USB
  works; this only matters if macOS ever claims the device.
- The phone UI for photos and drafting is verified by API and syntax check, but
  has not been exercised on a real handset.
- The same is true of the live-update client. The server half is tested through
  a real uvicorn/websockets stack, and the decision logic in `web/live.js` is
  tested directly, but `LiveChannel`'s wiring to a browser `WebSocket` — and
  the tap-target behaviour it exists to protect — has only been reasoned about,
  not watched on a handset.

The original build order and phase gates are kept for history in
`~/.claude/plans/create-a-packing-tracking-atomic-tarjan.md`.
