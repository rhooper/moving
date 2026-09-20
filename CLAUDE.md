# CLAUDE.md — Moving Box Tracker

Working notes for Claude sessions. See `README.md` for user-facing docs and
`docs/superpowers/specs/` for design rationale.

## Commands

```bash
make help                        # thin front door over everything below
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
make lint                        # ruff + black + oxlint + stylelint; `make fmt` reformats Python
make version                     # semver; the patch bumps on every commit, `make version-minor` by hand
scripts/claude/smoke.sh          # end-to-end against a running server
make browser-check               # EVERY browser check, writing ones too, on a throwaway server (~2 min)
make run & make ui-check         # the read-only ones, against a server you started; saves nothing
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

**Labels are landscape by default: a fixed 990 x 696 px** (3.3 in x 62 mm at
300 dpi -- the QL-800 is 300 dpi, and its 300x600 mode is not used), **all
identity and all fixed**. Top to bottom: box number (160 px) with the QR flush in
the top-right corner; a thin Code 128 of the box number under it; the room band
at `BAND_TOP` on *every* label; the summary (59 px); FRAGILE/HEAVY chips (72 px)
side by side, anchored to the bottom margin. This layout was specified element
by element on 2026-09-18 -- treat each of these as a decision, not a gap:

- **No dynamic font sizing.** A round of stepped scaling (1.5x down to 1.0x)
  shipped for a few hours and was rejected: same element, same size, on every
  label. `type_sizes()` exists so a test can pin that. The one exception is a
  *guard*: `_fit` starts at the fixed size and shrinks only when a code or room
  name physically cannot fit. It never triggers for the codes and rooms in use.
- **A loose item's name is set like any summary.** It used to be fitted as
  large as it would go, so "Bicycle" printed enormous.
- **The QR's size is its module size** (`QR_MODULE`, px per module). `_qr`
  snaps to whole pixels per module, so `target=210` and `target=242` both drew
  5 px modules: a requested "+15%" once changed a number and nothing on tape.
  6 px is the next real step (+20%). Two modules of quiet zone, not four: it
  sits flush in the corner and the tape's unprintable edge adds white.
- **OPEN FIRST is a double rule around the whole label**, run to the very
  edge, not a chip. It costs no layout room. Both lines plus the gap stay
  inside `MARGIN`.
- **The room band never moves.** Chips used to sit above it and push it down;
  they live at the bottom now and the summary stops short of them.
- **Code 128 (`labels/code128.py`) is hand-written** -- for a keyboard-wedge
  reader, payload is the box number only. The 107-row table was typed from
  memory, so `tests/test_code128.py` makes zbar read back *every* symbol
  value, including the eight that only occur as checksums. It starts a full
  ten-module quiet zone clear of the OPEN FIRST rule, and is skipped rather
  than shrunk if it cannot keep its quiet zone short of the QR. Any test that
  decodes a label must now filter by symbol type: there are two on it.
- **The itemised contents column was removed on request**: the list is one
  scan away in the app. `LabelData` has no `items`, and a test pins that
  itemising a box leaves its preview byte-identical. Consequence: a box with
  items but no summary prints with no description, so "From contents" is how
  to fill it. Source line, weight, box count and footer are also gone.
- Icons are PIL polygons in `layout.py`; no icon font dependency.

**The stub** (`render_stub`, `{"stub": true}` on print, `?stub=true` on
preview) is one inch of tape with just the number and the QR, for the moment a
box is created and still empty. It is 696 x 300 -- already the tape's width, so
`to_raster` passes it through and it reads *across* the tape, a quarter turn
from the main label. It is **exempt from the contents gate** (that is its
whole purpose) and it **does** bump `label_print_count`, because the purge
warning is about anything scannable stuck to a box. On the new-record form it
is the first button; Enter is explicitly routed to the plain Create so the
keyboard can never spend tape.

`make proof` is the test print without tape: stress cases plus `CODES="..."`
from the real database, one column at **1:1** (one pixel per printer dot, never
resampled -- barcode bars are 3 dots wide), drawn inline in iTerm via imgcat.

**Rotation belongs to the printer, not the layout.** `layout.render()` returns
an image that reads normally; `printer.to_raster()` turns a landscape design a
quarter turn so its 696 dots land across the tape. `build_instructions` rotates
*before* its width check — otherwise a correctly sized landscape label is
rejected for being 990 px wide. The rotation direction was settled on tape,
not in software; it is correct as written.

`orientation="portrait"` is the older cut-to-content form, still supported and
still tested, sizing between `MIN_HEIGHT` (300) and `DEFAULT_HEIGHT` (1063).
`MOVING_LABEL_ORIENTATION` selects the default.

**The printer is told never to sleep, once, at startup.** `AutoOffWatcher` (a
daemon thread started by the app's lifespan handler) polls for the QL-800 and
writes `400 x 0x00, ESC @, ESC i U A 00 00` — the framing i3labelstation uses
on the same model; verified live on this printer. The setting persists in the
printer's NVRAM, so the thread stops after one success rather than polling
forever. All USB access — this command and printing — goes through
`printer.exclusive()` (one `threading.Lock`), because FastAPI's threadpool
would otherwise let two jobs interleave rasters on the one device. The watcher
does nothing when the backend is `fake`, so tests never look for hardware.

**Anything that opens the USB device must dispose of it** (`usb.util.dispose_resources`).
The first version of the watcher did not, and it lives in the long-running
service: the service held the QL-800 exclusively from startup, brother_ql's
own open for every print job was refused, and printing failed with a
*permission* error -- the process locked out by its own leaked handle. The
tell is `ioreg -r -c IOUSBHostDevice -l | grep UsbExclusiveOwner` naming the
service's pid, and `is_kernel_driver_active(0)` reading True from any other
process. It is not a macOS permissions problem and no `sudo` fixes it.

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

**A live refresh is an interruption, and the PWA treats it as one.** The list
updates rows *in place* keyed by box code (`reconcile` in `web/live.js`):
rebuilding the markup would destroy every `<a>`, so a tap that began on one
lands on whatever node replaced it and you open the wrong box. On top of that
a refresh is held back entirely while a field is focused or dirty, while a
pointer is down, and for 600 ms after — the click has not landed when the
finger lifts. Held refreshes raise the `#live` banner instead, and the new-box
form and scanner are never refreshed at all (`affects()` matches no view name
for them).

**Three parts of an open record update in place, even while a whole refresh is
held**: the contents list (`items.changed`), the photo strip (`photos.changed`)
and a *pristine, unfocused* summary (`box.updated`) -- `partOf()` in `live.js`
maps the events, `itemsPart`/`photosPart` in `app.js` draw through `reconcile`
so the first draw and every update share one path, and listeners are attached
when a row is created so there is no rebind step to forget. This is what lets
photo analysis add items while you are typing somewhere else. They still wait
out a pointer-down and the settle; they never touch a focused or dirty field.
`viewBox` counts whole-page draws in flight and re-requests a part that changed
during one, so a stale draw cannot overwrite it. **Rows a part creates must
carry `data-key`** -- `reconcile` sets it now, because photo figures once did
not, and every update drew a second copy of the strip.

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

**Vision is local-only by deliberate choice**, through Ollama. A cloud provider
would slot in behind `vision.base.VisionProvider`; none is built, because local
was the choice.

**A quick model reads every photo; a careful one looks closer when asked.**
`qwen3-vl:4b-instruct` by default (`MOVING_VISION_MODEL`), `qwen3-vl:8b-instruct`
for the closer look (`MOVING_VISION_DETAIL_MODEL`, the **Look closer** button in
the photo viewer, `POST /photos/{id}/analyse?detail=true`). Measured on this
machine on the owner's own photos, through the app's own request and parser
(2026-09-18; raw data was in a scratch dir, the numbers that matter are here):

| model | per photo | notes |
|---|---|---|
| `qwen3-vl:30b` (the old default) | 37.5 s median, up to 127 s; 87 s cold | vague names, garbles handwriting |
| `qwen3-vl:4b-instruct` | **7.4 s** median (n=69); ~9 s through the app | 0/73 parse failures; the most specific names |
| `qwen3-vl:8b-instruct` | 10 s; ~15 s through the app | best at handwriting and brand spelling |
| `qwen3-vl:2b`, `gemma3:4b` | fast | not usable: runaway generation; invented items |

- **The 30b was slow because it *thinks*, not because it is large.** Every bare
  qwen3-vl tag (`:2b`, `:4b`, `:8b`, `:30b`) is the *thinking* checkpoint; it
  wrote a median 1,800 tokens of reasoning (~30 s) before each answer, 27,000
  characters about a photo of two closed boxes -- and was not more accurate for
  it. **The `-instruct` in the tag is what matters.** `/no_think` in the prompt
  does nothing. `"think": false` works but, with `format` set, Ollama 0.34 puts
  the JSON in `message.thinking` and leaves `content` empty, so `read_response`
  falls back to `thinking` (42 of 42 replies were being lost that way).
  `qwen3-vl:30b-a3b-instruct` (20 GB) is the obvious untested candidate for an
  even better closer look.
- **`keep_alive: "30m"` and `num_ctx: 8192` go on every request.** Ollama
  unloads after five idle minutes and boxes are often further apart than that;
  and left alone it sizes the context at 262k, so a 4b model takes 25 GB (under
  4 GB at 8192, exactly as fast). Both models fit in memory together.
- **Photos stay at 2048 px.** At 1024 the 4b is 3x faster and stops reading
  small text (zero items on a cabinet of labelled drawers) and the models start
  inventing things. It is the only big speed lever left, and a bad trade.
- **A closer look adds; it never removes.** It is merged like any other read, so
  a quick read's misreading ("Tiny Relays") stays beside the closer look's
  correction ("Tiny Bulbs") until someone deletes it. Items carry no link to
  the photo or job they came from; that link is what replacing would need.
- `ai_jobs.detail` records which kind a job was (migration 0007): it cannot be
  inferred from the model name, which is configuration. Each model is timed
  against its own history, so a slow closer look never lengthens the quick
  read's countdown.
- **A photo described but not itemised still gives the record a summary.** A
  cabinet of labelled drawers came back as one good sentence and no items, and
  the record was left blank; the model's sentence is now the fallback when
  there are no items to build a summary from. Items win as soon as there are any.

**Every uploaded photo is analysed in the background, and what is found is
applied** (`analysis.py`; spec in `docs/superpowers/specs/2026-09-18-photo-analysis.md`).
This *reverses* the older "never auto-apply a draft" rule, on request, for
photos only -- `ai.draft_for_box` is still a proposal. What survives is the
half that protected people's work, and each clause has tests:

- the model's items are `source='ai'` (shown as *autogenerated*); an item a
  person typed is never renamed, re-counted or removed;
- a summary a person typed is never overwritten -- only an empty one or one
  that is itself autogenerated (`boxes.summary_source`). `update_box` with a
  `content_summary` marks it `manual`; clearing it hands it back. Every summary
  that existed before migration 0006 counts as a person's;
- renaming an autogenerated item (`PATCH /api/items/{id}`) makes it a
  person's. `summary_source` and `source` are never client-settable.

How it runs, and why:

- **One worker thread, one job at a time.** The model is the bottleneck; two
  calls at once only make each slower. `Analyst.run_once()` is the whole of the
  logic and is what tests drive, on their own thread -- the thread around it
  only decides when to call it.
- **Jobs live in `ai_jobs`** (rows carry `photo_id`; a photo's state is its
  *latest* row, and a re-run adds a row rather than replacing one -- finished
  rows are the history the estimate is made of, and deleting them on re-queue
  once reset it to the default), not in memory, because every deploy restarts
  the service: `recover()` puts `running`
  jobs back to `pending` on startup. `photo_id` has no foreign key on purpose --
  SQLite will not DROP a column that is part of one, and migration tests roll
  back by dropping -- so `storage.delete_photo` removes a photo's jobs itself.
- **Merging matches on a normalised name** (case, spaces, a trailing plural).
  Deliberately crude: a missed merge is a near-duplicate someone can delete; a
  wrong merge loses an item. A better count raises an autogenerated quantity;
  a worse one never lowers it.
- **The estimate is the median of the last ten runs for the model**, not the
  mean, so one cold start does not make every later photo look slow; a queued
  photo's wait includes the jobs ahead of it. With one or two runs of history
  it can be badly off, which is why the UI's ring goes indeterminate past zero
  instead of sitting full.
- **The thread starts only if `config.auto_analyse`**, which `from_env` turns
  on and the dataclass default leaves off. Every test builds a `Config`
  directly, so nothing in the suite can reach a model;
  `test_the_worker_does_not_run_in_the_test_suite` guards that.
- `MOVING_VISION_PROVIDER=stub` (+ `MOVING_VISION_STUB_SECONDS`) is a
  deterministic provider for checking the UI. Events from the worker carry no
  `origin` -- no device caused them -- so every client, including the uploader,
  acts on them.

**`base.parse` is deliberately forgiving of the reply, strict about the
outcome.** Local models wrap JSON in markdown fences, prepend "Sure! Here
is…", and return quantities like `"lots"` however firmly the prompt forbids
it — all of that is mined and coerced. But a reply with no usable JSON raises
`DraftUnreadable` rather than returning an empty draft, which would read as
"the model saw an empty box".

**Things go inside things** (`boxes.parent_id`, migration 0009; `set_parent`,
`children_of`, `path_to` in `store.py`; `parent_code` on POST/PATCH). A record
may be inside one container -- a bag in a box in a crate, to any depth -- and
the record carries `children` (shaped like list rows), `path` (outermost first)
and `parent`. The store keeps the pointer honest since it has no foreign key:
a parent must exist, be a container (`holds_contents`), not be binned, and not
be inside the thing being moved; **a container holding things can neither be
deleted (409) nor become a single thing**. Browsing shows the top level only
(`parent_id IS NULL`); **search looks everywhere** and each row carries
`parent_code` to say where. What is inside counts as contents for the print
gate. Moves announce `box.updated` for both containers. Nested records keep a
code -- they are opened and scanned like any other -- but nothing prints unless
asked; "generally won't have a label". Deleting a nested record and restoring
it puts it back where it was.

On the page (`web/nesting.js` holds the pure rules, tested): a container shows
an "Inside this crate" section of rows drawn like the list (`rowFor`/`fillRow`
through `reconcile`, updated in place from the same `box.updated` fetch as the
summary); a nested record shows the breadcrumb out above its code; "What it is
inside" is its own section -- a container relationship is not a room -- with a
lookup field that takes a typed or **scanned** code, previews the container,
then "Put it inside" / "Take it out", autosaved as `parent_code` with "Undo
move". `mayHold()` refuses on the page what the server would refuse (itself,
its own child, a single thing, the bin) so the 422 is the exception, and a
refused move is not retried by Undo either -- it was, once. Delete is withdrawn
and the single-thing kinds greyed (`segmented.restrict()`, real `disabled`,
arrow keys skip) while things are inside. `#/new/in/CODE` is the new-record
form pre-set inside a container, plain Create first. The list says "3 inside";
a nested search result says "in B-0012" on a line *above* the row's rule, which
is why that rule moved from the `<a>` to the `<li>`. The record carries `parent`
(a step) but no `parent_code` field; the page keeps a hidden `parent_code` input
for the autosaver and reads `fresh.parent` on landing.

Two more, asked for as "subitems should hide the destination input" and
"fragile should percolate up to the parent and set that (prompt to set if it's
not set) but don't undo on clear". Each step of `path` carries the container's
`destination_room_id`, `fragile` and `size` for these:

- **A nested record goes where its container goes.** The nearest container with
  a room decides (`inheritedRoom()` in `nesting.js`; `store.going_to()` for the
  label), *over* a room of the record's own -- a box for the garage put in a
  crate for the kitchen is going to the kitchen. Its own room stays in the
  database and its row comes back when it is taken out. On the page the
  destination row is always built and hidden *and disabled* (a disabled
  fieldset's radios stay out of FormData) whenever the record is nested, from
  `showRoom()`, which `showInside()` drives -- so it comes and goes in place on
  a move, never by a redraw. The band and "Goes where B-0002 goes: Kitchen"
  say whose room it is; the thin-label question counts it; `#/new/in/CODE` has
  no destination row at all.
- **Fragile climbs, never descends, and clearing never climbs.** Marking a
  nested record fragile, putting a fragile one inside something, or creating
  one inside with Fragile ticked (from the `path` in the 201) offers, through
  `confirmed()` ("Mark them fragile" / "Not now"), to mark the containers that
  are not yet (`notYetFragile()`): one PATCH each from the page, then the
  page's own copy of `path` is marked so the same containers are not asked
  about twice. "Not now" is not nagged about until the next trigger. Turning
  Fragile off touches nothing else, and the containers' marks are their own
  writes, outside the record's Undo. The server changes nothing by itself: the
  climb is the prompted choice that was asked for. `nesting_check.mjs` presses
  all of it, the dialog included.
- **"Add something inside" is a dialog, not a page** (`addInside()` in
  `app.js`; the decisions in `nesting.js`: `kindsToAddInside`,
  `addInsideRequest`, `addedInside`). Asked for as "optimize workflow for
  sub-items: Adding a subitem should pop up a dialog that asks for type and a
  photo and an optional source. The rest of the activities can be done from
  the ui." Somebody at an open crate drops bags in without leaving the crate's
  page: a native `<dialog>` on `document.body` (so a live refresh underneath
  cannot take it away, and the autosaver's hold never counts its fields) asks
  for the kind (every kind -- a crate holds a bag or a lamp), a photo (the
  same camera input as `#shot`, the filled button; optional, and said to be)
  and a source room, and nothing else: no summary (the photo is read in the
  background and names the contents), no destination (it goes where the
  container goes), no size, no flags. Add creates and stays, the row arriving
  through the same `box.updated` refetch as any other (asked for explicitly,
  since the page's own write comes back as an echo the socket drops) and a
  line under the section naming it with a link; Add and open goes to it;
  Cancel, Escape and the backdrop make nothing. A photo that fails to upload
  leaves the record standing and the dialog open saying so, its buttons turned
  into "Try the photo again" / "Open it" -- neither a half-made thing nor a
  photo silently dropped. `#/new/in/CODE` still exists for the full form.

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
- **`/sw.js` is served with its cache version substituted to the deployed
  revision** (route in `app.py`, tests in `test_service_worker.py`) — never
  hand-bump `VERSION` in `web/sw.js` again. It was a hand-bumped literal once,
  nobody bumped it, and deployed phones ran a stale app.js against the new API
  until buttons errored. Dev (revision `unknown`) serves the file as written.
- **Every asset URL carries the deployed revision** (`api/assets.py`, tests in
  `test_cachebust.py`): `/app.js?v=<rev>` in index.html, in the worker's shell
  list, and in the `import` lines *inside* each module -- a versioned entry
  point importing unversioned modules busts nothing. A URL with the current
  revision is `immutable` for a year; anything else (index.html, sw.js, an old
  `?v=`) is `no-cache`. This closed the hole the sw.js fix left: the worker's
  shell re-fetch could be answered by the browser's HTTP cache with last
  week's app.js, which is why deploys needed "reload it twice". Not `sw.js`:
  a worker is identified by its script URL. Dev serves files as written.
  `MOVING_REVISION` names a revision for a throwaway server, and
  `browser_checks.sh` sets it, so the checks load the app the way production
  serves it rather than the one way it never does.

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
  (`summarise.from_items`). The "From contents" button puts one in the field,
  which autosaves like any edit (Undo restores the old one) and so becomes the
  person's. Photo analysis *applies* one, but only to a summary that is empty
  or already autogenerated. Plain assembly either way:
  instant, identical every time, offline.
- **AI output is provenance-tagged**: items the model adds get `source='ai'`.
  Whether AI output is *applied* depends on the path -- see "Every uploaded
  photo is analysed" above. `ai.draft_for_box` proposes and writes nothing.
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
- **Exactly one cover photo per box**, enforced by a partial unique index
  (`idx_photos_one_cover`, migration 0003) rather than by convention —
  `photos.is_primary` predates it and nothing kept the *one* part. Two
  consequences: `storage.set_cover` demotes before it promotes, and
  `delete_photo` promotes a survivor **only when the deleted photo was the
  cover**. Promoting unconditionally was harmless while the cover was always
  the oldest photo; once it is a choice it both changes the picture silently
  and leaves two flagged rows, which the index now refuses.
- **`list_boxes` carries `cover_photo_id`**, so a screenful of rows draws
  thumbnails without a request per row. It is a correlated scalar subquery in
  the same statement, and the partial index above is what makes it a covering
  index seek (the photo id is the index's own rowid) — bounded by `limit`, not
  by the size of the photos table. `/api/boxes` and `/api/search` both go
  through it, so there is one place to change.
- **`LIST_KINDS` in `web/live.js` now includes `photos.changed`.** It was
  deliberately excluded while a photo changed nothing the list drew; the cover
  thumbnail put a photo *on* the row, so leaving it out meant the other phone
  kept showing a stale picture or a blank square.
- **`showError()` is for a view that could not be drawn; `failed()` is for an
  action that failed on a page that is still good.** `failed()` raises a native
  `<dialog>` and leaves the page, scroll position and typed text alone. Using
  `showError()` in an action handler costs the user their place -- that was
  reported for printing and was true of every action.
- **Every `<form id>` drawn in `web/app.js` needs a submit listener**, and
  `tests/test_web_forms.py` enforces it. A form without one still submits --
  natively: page reload, fields in the query string, nothing saved. The box
  page's summary and destination forms shipped that way and never saved once.
  That is the third half-landed patch in `app.js` (after `splitItems`);
  there was no JS linter then. There is now (oxlint, `make lint`), and the
  module-mode syntax test; these static guards remain because they check
  things a linter does not (a drawn form with no listener).
- **Anything that hides or destroys asks first, through `confirmed()`** (a native
  `<dialog>`, never `confirm()`): box delete, permanent delete, photo delete.
  Cancel holds the focus so a stray Enter or double tap lands on the safe
  answer; Escape and the backdrop are "no". Removing a single item from a
  box's list deliberately does **not** ask -- it is one tap to re-add, and a
  modal per row would make tidying an AI draft miserable.
- **A brand-new database can 500 once.** `db.migrate()` runs on every connect,
  and a page load fires two requests in parallel; on a database with no schema
  yet, both try to migrate and one gets `database is locked`. It cannot happen
  to the live database (already migrated). It bit a throwaway test server;
  pre-migrate with any CLI command (`moving seed-rooms`) before pointing a
  browser at a fresh `MOVING_DB_PATH`.
- **A throwaway server is how write paths get checked**: set `MOVING_DB_PATH`,
  `MOVING_PHOTO_DIR`, `MOVING_LABEL_PREVIEW_DIR`, `MOVING_BACKUP_DIR`,
  `MOVING_PRINTER_BACKEND=fake` *inline on the command*, pre-migrate, serve on a
  spare port. From a worktree this matters doubly: `config.ROOT` follows the
  package, so a worktree's dev server already has its own empty `var/`, and the
  live database is never in reach by accident.
- **The record page saves itself; there is no Save and no Cancel**
  (`web/autosave.js`, DOM-free and tested in `tests/autosave.test.mjs`; the
  wiring is `editSession` in `app.js`). Requested as "autosave edits with undo,
  as opposed to manual save", replacing a Save/Cancel pair that lasted a day.
  - *When:* pickers save on change; text after a 1.2 s pause and on leaving the
    field; **the current location only on blur or Return** (`data-autosave="commit"`)
    because each save is written to the box's history and a pause mid-word
    would log "garage st" as a place it had been.
  - *A save never redraws the page* -- that would take the caret out of a
    sentence. It updates `box` in the closure and the field's `dataset.initial`,
    which is also what tells the live-refresh logic the field is clean again.
    `kind` is the exception: it changes which sections exist, so it redraws.
  - *Undo* is one bounded stack across fields, newest first, offered on the
    status line of the form last edited and named for what it restores ("Undo
    summary"). An undo is not itself undoable.
  - *A failed save never raises a dialog at someone typing*: the line says "Not
    saved yet", the edit is kept, and it retries by itself (2 s doubling to
    30 s, and when the browser comes back online). A refusal (4xx) is not
    retried.
  - *Nothing pending is lost:* leaving the record, hiding the tab and `pagehide`
    all commit what is waiting, and a whole-page redraw commits, **waits for the
    save to land**, then redraws -- commit-then-redraw was not enough, because
    the redraw fetched the box before the PATCH arrived and adopted the old text
    as the baseline.
  - *One session per open record, at module level,* because the saver has to
    outlive the redraws that happen under it (a chip, a print, a kind change).
  - Code that sets a field's value (From contents, dictation, a live update of a
    pristine summary) must tell the saver: an edit for the first two, `track()`
    for the third -- a server-side change is a new baseline, not something to
    save back. **"From contents" therefore saves at once**, with Undo; it used
    to land in the field and wait for Save.
  - The status line is always drawn at a fixed height so it appearing never
    moves the page under a thumb, and Undo sits at the far end of it: it once
    slid sideways when the text beside it changed, so the press and the release
    landed on different things.
  - Scope is the record page. The new-record form keeps its Create buttons
    (nothing exists to save into) and Settings keeps explicit Saves (a code
    prefix is not something to change by brushing a field).
- **Tapping a photo opens a viewer with what the model saw in *that* photo**
  (`viewPhoto` in `app.js`, `seenIn` in `covers.js`; the photo's `analysis`
  carries `items` and `summary` from its own job). Not the record's merged list:
  this is the evidence for one picture, so a wrong item can be traced to the
  photo it came from. A figure is built once and updated in place, so the strip
  keeps each figure's latest photo in a WeakMap (`lastHeard`) and repaints an
  open viewer -- open one while a photo is still being read and it fills in by
  itself. The thumbnail is still a real link: a long press or middle click opens
  the file as before.
- **The app's word for a record is "item"** (nav: Items / Scan / New / Settings;
  "6 items"), since a record may be a box, a tub or a loose thing. "Box" survives
  where it means the *kind*. Beware the overlap: `kind = "item"` is one kind of
  item, and the things inside a box are still "items" in the code (`items`
  table, `items.changed`). The Python package, API paths (`/api/boxes`) and
  table names were deliberately **not** renamed -- labels in circulation and
  the QR URLs (`/b/CODE`) depend on them, and a rename buys nothing.
- **A list row's last cell is what it is, over how far along it is**
  (`rowStatus` in `covers.js`). One function on purpose: the first draw used to
  show the kind while the live-update path overwrote it with the location, and
  the two disagreed silently. The current location is no longer in the list; it
  hid the status, and it has the whole record page.
- **`node --check web/app.js` proves nothing.** On a `.js` file containing
  `import`, Node 23.3 exits 0 without parsing it as a module, so a missing brace
  passes. It was the syntax guard for a day of patches before a subagent
  noticed. The working form is `node --input-type=module --check < file`, and
  `tests/test_web_syntax.py` runs it for every module in the deploy gate -- with
  a test that the check *can* fail, which is the property the old one lacked.
- **How many labels print is per kind of thing** (`kinds.KINDS[...]["copies"]`,
  overridable per kind in Settings via `PUT /api/settings/kind-copies`; read
  through `prefs.label_copies(conn, kind)`). Two for a box, tub or crate --
  they get stacked and more than one face is seen -- one for a bag, a loose
  item or furniture. A print request may say otherwise; a stub prints one; a
  mixed batch prints each at its own number. `label_print_count` counts
  *labels*, not button presses. There was one global number for two days;
  nobody set it, and one number cannot be right for a crate and a lamp.
  `/api/settings/kinds` carries `copies` and `sizes` per kind, so the record
  page (which fetches it for the picker anyway) needs no extra request; the
  copies field there starts at the record's kind's number and is a choice for
  one print, not part of the record -- its `dataset.initial` follows its value
  so it never looks half-edited and holds back live updates.
- **A container may have a size** (`boxes.size`, migration 0008: small, medium,
  large, extra large -- `kinds.SIZES`, no CHECK, like `kind`). Only something
  that holds contents: a single thing is refused one (422), and a container
  that becomes a single thing loses it, since "large lamp" means nothing and
  the picker that could clear it is gone by then. The list row says "large
  box" / "XL crate" (`rowStatus`).
- **Kind, size and the two rooms are pushbutton rows** (`web/segmented.js`, one
  builder; `pressed()` is the pure clear-on-second-press rule, tested): native
  radios in a `<fieldset>`, each `<label>` drawn as a joined button, so
  `FormData` and the autosaver's `change` handling need no glue. An optional
  row **clears on a second tap of the selected button** -- that is what the
  owner meant by "double tapping to clear"; the kind is required and ignores
  it. Three things found by pressing: Chrome sends no click for Space on an
  already-chosen radio, so keyboard users could select but never clear
  (handled on keydown); a lone last button on a wrapped row stretched into a
  full-width bar (the last line keeps its natural width); and **a focused
  radio swallowed a barcode scan** -- see the wedge bullet. The new-record
  form's destination row lists destination rooms only, as the record page
  always did. Known: Undo of a kind change brings the kind back but not the
  size the server cleared.
- **Printing a thin label asks first** (`confirmThinLabel`): no contents, or no
  destination room. It replaced the "print anyway" tick box. The server still
  refuses an empty box without `allow_empty`, and the UI only ever sends that
  after a yes. The new-record form asks *before* creating, so "no" leaves you
  on the form with nothing made. The stub is exempt by design.
- **A barcode reader is a keyboard** (`web/wedge.js`, `openEntered` in
  `app.js`). It types what it scans and presses Return: the label's Code 128
  is the box number, its QR is the box URL. A box URL can only have come from
  a label, so it opens without asking; a bare number is only *shaped* like a
  code -- so is "kettle" -- so it is looked up first and falls back to a
  search. It works in the search box, and at the page with nothing focused
  (`KeyBuffer` collects the keys, since the reader has no idea where the
  cursor is). Two traps it handles, both pinned by `wedge_check.mjs` with real
  key events: **a focused button** -- whichever was tapped last -- would be
  pressed by the reader's Return, so after "Print label" a scan would spend
  tape; the Return is swallowed when it completes a scan. And **Firefox opens
  quick find on "/"** when nothing is focused, which would eat a scanned URL;
  "/" is suppressed only while a scan is under way. Scanning a binned record
  opens it, where Restore is offered -- same as the `/b/` redirect. **A radio
  or a tick box counts as a button, not a field**, for this purpose: when a
  pushbutton row had the focus, the listener ignored the scan as "typing" and
  the reader's Return submitted the form it sat in -- on the new-record form
  that created an empty record.
- **Run `make browser-check` after touching `web/app.js`.** It starts a
  throwaway server (own database, fake printer, stub vision provider) and runs
  every browser check against it: `ui_check` and `wedge_check` (read-only, and
  they assert they wrote nothing -- these two also run against the live service
  after a deploy), and the ones that write -- `autosave_check` (real typing,
  pauses, blur, Undo, failed saves made at the network layer), `copies_check`
  and `viewer_check` -- which **refuse port 8787 and any non-loopback host**.
  All drive headless Chrome over CDP with Node's built-in WebSocket, no npm.
  The static guards cannot see an undefined variable inside a click handler;
  these can, and have caught: a stale element reference left by a merge, a
  duplicated photo strip, a `route()` that never told the record it was being
  left, and an Undo button that moved under the finger.
  Harness traps, all met the hard way: give each Chrome its own debugging port
  and `--user-data-dir` (every check honours `CDP_PORT`; `make browser-check
  CHECK_PORT=8801 CDP_PORT=9366` keeps two runs apart) and **never `pkill`
  Chrome by pattern** (agents run in parallel and kill each other's); `--screenshot` plus a debugging port never
  exits; a narrow `--window-size` does not narrow the layout (use an iframe or
  `Emulation.setDeviceMetricsOverride`); headless Chrome follows the Mac's dark
  mode; a headless page fires no blur events without focus emulation.
- **`el.hidden` only works because of the `[hidden] { display: none !important }`
  rule** in `index.html`: the UA's own rule loses to any author `display`.
- **The version is semver and bumps itself.** `src/movingbox/version.py` is the
  only place the number lives: pyproject reads it (hatchling dynamic version),
  the API serves it (`/health` has `version` beside `revision`: the revision
  says which commit is running, the version is for a person describing a bug).
  The **pre-commit hook bumps the patch on every commit**, staging only that
  one file. A commit that stages `version.py` itself is left alone -- which is
  what `make version-minor` / `version-major` rely on to land exactly on
  `x.Y+1.0`. Hooks live in the common git dir, so worktrees bump too, each its
  own file; two branches bumping the same line will conflict on merge, and the
  resolution is always "take the higher, then let the merge commit bump it".
  `make setup` installs the hooks; a fresh clone without them simply stops
  bumping, silently -- run `make setup`.
- **`make lint` is four linters**: ruff and **black** (Python; black at ruff's
  line length of 100 -- `make fmt` reformats), **oxlint** (JS, via npm; it
  covers `web/` and the check scripts, skipping vendored jsQR), and
  **stylelint** on the `<style>` block of `web/index.html` (there is no
  separate stylesheet; `postcss-html` reads it in place). `package.json` exists
  for these two linters only -- the app still has no JS build step and ships
  nothing from `node_modules`. The stylelint config switches *off* the
  whitespace and notation rules that fight this stylesheet's deliberate
  one-line-rule idiom, and keeps the ones that find mistakes: its first run
  found a duplicated `dialog p` rule. Two vendor prefixes are inline-disabled
  with the reason (Safari has no unprefixed `mask` or `text-size-adjust`).
  Beware `currentColor`: stylelint wants it lower-cased *in CSS*, but the same
  word in the home mark's SVG `fill` attribute is markup and must stay as is.
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

**The home mark** (top-left on every page; `a.home` in `index.html`, source in
`docs/design/`) is the label's QR finder pattern -- the square a scanner uses
to find a code's corner and know which way is up -- with its core cut as a
house. It sits in the app's own top-left corner as the origin, and it also
reads as a home packed in a box. One evenodd path on a 16-unit grid with every
coordinate even (ring 2, gap 2, house 8), so it is pixel-exact at 16, 24 and
32 px -- which is why it is drawn at 32 on a phone and 24 in the desktop bar,
never 28. Inline SVG so `currentColor` follows the theme; true black, no
radius, no accent. Chosen over "the label as an object" (read as a dashboard,
or as "print") and the ISO this-way-up arrows (read as "upload"). Not yet used
as the PWA icon, which is still a white bar on black and reads as a minus sign.

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

Since then (2026-09-18, 422 tests): box/item/tub kinds with per-prefix
configurable codes, cover photos, soft delete with a bin, handling-flag
toggles (fragile/heavy/open-first) that print as icon chips, the printer badge
in the nav bar, the auto-power-off watcher, and the 3-inch label redesign.
Remote: **github.com/rhooper/moving (private)** — push after merging to main.

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
