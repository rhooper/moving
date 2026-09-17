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
uv run moving serve                    # http://localhost:8787
```

The camera will not work over plain HTTP on a LAN address — see **HTTPS is
mandatory** below.

## HTTPS is mandatory

`getUserMedia` and `BarcodeDetector` are **secure-context only**. Served over
`http://192.168.x.x:8787` the camera silently fails: no scanning, no photo
capture, no PWA install prompt. `localhost` is exempt; a LAN IP is not.

Tailscale issues a real certificate, which solves this:

```bash
tailscale serve --bg 8787
```

The app is then reachable at `https://moving.example.ts.net`, which
is also the base URL encoded into every printed QR code.

> **The base URL is baked into printed tape.** Changing the hostname later means
> reprinting labels. The in-app scanner is tolerant — it strips the host and
> reads the trailing code — so a change would break only the stock-camera
> tap-through, not lookup.

## Labels

62 mm continuous DK-2205, black only, 696 printable dots at 300 dpi. Default
label is 62 × 90 mm.

During development the printer backend defaults to `fake`, which writes a PNG to
`var/labels/preview/` instead of burning tape. Tests force it.

```bash
MOVING_PRINTER_BACKEND=brother_ql uv run moving print B-0001
```

## AI contents drafting

Photograph the open box before taping it; a local vision model drafts the item
list and summary, which you then edit. Requires Ollama:

```bash
ollama pull qwen3-vl:30b     # default, ~20 GB
ollama pull qwen3-vl:8b      # faster option
```

Drafts are **never** applied automatically. Accepted items are stored with
`source='ai'` so AI-derived data stays distinguishable from what you typed.

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

## Layout

| Path | Contents |
|---|---|
| `src/movingbox/` | FastAPI service, label rendering, vision providers |
| `web/` | PWA — no build step, plain ES modules |
| `migrations/` | Numbered SQL, applied against `PRAGMA user_version` |
| `scripts/claude/` | Operational scripts (backup, print test, model pull) |
| `docs/superpowers/specs/` | Design documents — never deleted |
| `var/` | Database, photos, label previews. Gitignored, backed up separately |
