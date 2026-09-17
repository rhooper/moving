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
uv run pytest                    # printer forced to `fake` in conftest
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

**`cv2.QRCodeDetector` cannot be trusted** — it failed to decode a valid
version-3 QR that was pixel-identical to segno's own reference render. Tests use
**zbar** (`brew install zbar`), the engine real scanners are built on. pyzbar
finds libzbar via `ctypes.util.find_library`, which does not search Homebrew's
prefix; `tests/conftest.py` sets `DYLD_FALLBACK_LIBRARY_PATH` in `os.environ`
before the import. That works where `DYLD_LIBRARY_PATH` would not, because
ctypes reads it at call time whereas dyld caches its own at exec.

**Fonts are bundled** (`labels/fonts/Inter.ttf`, OFL). Golden-image tests
compare rendered bytes, so a system font update would break them spuriously.

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
  `source='ai'`. Never auto-apply a draft.
- Scripts live in `scripts/claude/` with a purpose header.
- Ruff's `B008` is disabled for FastAPI's `Depends`/`Query`/`Header` defaults
  via `extend-immutable-calls` — it is a false positive for that idiom.

## Domain model gotcha

`destination_room_id` and `current_location` are **different things** and both
matter. A box destined for the kitchen may currently be on the truck.
Conflating them breaks the main use case ("where is the coffee maker *right
now*?").

## Status

Phases 0–4 complete: schema, store, REST API, search, label renderer, printer
backends, CLI. 90 tests passing. Verified end to end against a running server
via `scripts/claude/smoke.sh`.

**Phase 4 physical checkpoint is still outstanding** — no QL-800 is attached
(`lpstat -p` shows only an MFC-L3770CDW and an Epson P900). Before building the
PWA on top of this layout, print one real label on DK-2205 and scan it with a
phone. Layout changes are free on screen and expensive once on tape.

Next: phases 5–9 (PWA shell, scanner, admin, AI drafting, backups/deploy). Build
order and gates are in
`~/.claude/plans/create-a-packing-tracking-atomic-tarjan.md`.
