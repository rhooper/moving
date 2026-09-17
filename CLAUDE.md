# CLAUDE.md — Moving Box Tracker

Working notes for Claude sessions. See `README.md` for user-facing docs and
`docs/superpowers/specs/` for design rationale.

## Commands

```bash
uv sync                          # install deps (Python 3.13)
uv run moving serve              # dev server on :8787
uv run pytest                    # tests — printer is forced to `fake`
uv run ruff check src tests      # lint
tailscale serve --bg 8787        # HTTPS, required for camera access
```

## Hard-won facts

**HTTPS is not optional for the PWA.** `getUserMedia` and `BarcodeDetector` are
secure-context only. On a LAN IP they fail *silently* — no error, just no
camera. `localhost` is exempt. Use `tailscale serve`; it provisions a real cert
for `moving.example.ts.net`.

**Label geometry, verified against `brother_ql.labels`, not assumed:**
label id `62`, `FormFactor.ENDLESS`, `dots_total=(732, 0)`,
`dots_printable=(696, 0)`. Render at **696 px wide**. Height is free (endless
tape); default 1063 px ≈ 90 mm at 300 dpi.

**`brother_ql` is stale.** It emits
`brother_ql.devicedependent is deprecated` on import. It works, and QL-800 is in
`ALL_MODELS`. If it breaks on a future Python, the drop-in is the GPL-3.0 fork
`luxardolabs/brother_ql` (typed, 3.13+). All printing goes through the
`PrinterBackend` protocol in `labels/printer.py` so that swap touches one file.

**Fonts are bundled, not system-resolved.** Golden-image tests compare rendered
labels byte-for-byte; a system font update would break them spuriously.

## Conventions

- **Printer backend defaults to `fake`** everywhere except explicit real prints.
  Tests set `MOVING_PRINTER_BACKEND=fake` via pytest config. Never let a test
  reach hardware — it wastes tape and needs the printer attached.
- **Migrations are numbered SQL** applied against `PRAGMA user_version`. No
  Alembic; single-writer SQLite does not need it. Never edit an applied
  migration — add a new one.
- **FTS is maintained explicitly**, not by triggers: call
  `search.reindex_box(conn, box_id)` after any mutation touching a box, its
  items, or its photos. `reindex_all()` rebuilds. Chosen over triggers because
  it is directly testable.
- **AI output is provenance-tagged.** Items created from a vision draft get
  `source='ai'`. Never auto-apply a draft.
- Scripts live in `scripts/claude/` with a purpose header. Not in the repo root.

## Domain model gotcha

`destination_room_id` and `current_location` are **different things** and both
matter. A box destined for the kitchen may currently be on the truck. Conflating
them breaks the main use case ("where is the coffee maker right now?").

## Status

Phase 0 complete: repo, deps, HTTPS path confirmed, label geometry verified.
Build order and phase gates are in
`~/.claude/plans/create-a-packing-tracking-atomic-tarjan.md`.

Phase 4 (real printing) is a **hard checkpoint** — print one physical label and
scan it before building anything further on top of the layout.
