# CLAUDE.md — Moving Box Tracker

Tracks what went into which box during a house move: a FastAPI + SQLite
service, a PWA with no build step (plain ES modules), QR labels printed on a
Brother QL-800 and scanned with a phone. User docs: `README.md`. Photo-analysis
API contract: `docs/superpowers/specs/2026-09-18-photo-analysis.md`. Icon
design record: `docs/design/icons/NOTES.md`. How each decision was reached is
in `git log`.

## Commands

```bash
make setup                       # uv sync + npm ci (linters only) + git hooks
make run                         # dev server with reload on :8788 (the live service owns :8787)
make check                       # lint + test; `make test`, `make lint`, `make fmt` (black) alone
make browser-check               # EVERY browser check, writing ones too, on a throwaway server (~2 min)
make run & make ui-check         # the read-only browser checks, against a server you started
make proof                       # label proof sheet at 1:1 in iTerm; CODES="B-0003 ..." adds real ones
make version                     # semver; the patch bumps on every commit, `make version-minor` by hand
make help                        # the rest: labels, status, backup, install ...
uv run pytest                    # also runs tests/*.test.mjs through node --test
uv run moving serve              # 127.0.0.1:8787 by default
uv run moving seed-rooms         # starter rooms; also pre-migrates a fresh database
uv run moving preview B-0001     # label PNG, no printing
uv run moving print B-0001       # honours MOVING_PRINTER_BACKEND (default fake); not gated on contents
uv run moving reindex            # rebuild the FTS index
uv run moving thumbnails         # strip image for every photo lacking one (--dry-run, --prune)
uv run moving backup | export --format csv -o out.csv | manifest
scripts/claude/deploy.sh         # backup, test, restart, verify -- normally run by the post-merge hook
scripts/claude/install-service.sh [--uninstall]  # launchd agent, nightly backup, tailscale serve, hooks
scripts/claude/smoke.sh          # end-to-end against a running server
scripts/claude/try_vision.py     # the local vision model directly (slow, non-deterministic)
scripts/claude/try_summary.py    # the summary model over real contents lists, timed
scripts/claude/try_cloud_vision.py  # the cloud tier through a running throwaway app (spends money)
tailscale serve --bg 8787        # HTTPS, required for the camera
```

## The API key is the app's, not the assistant's

**Mandatory restriction, stated by the owner on 2026-09-20:** "you are not
allowed to use this api key directly... you may not use this api key for
anything but the specifically designed operations for classifying images and
summarizing text to do with the user interactions in the UI."

The Anthropic key lives in `.env` (gitignored, mode 600) and belongs to the
running application. Its only sanctioned uses are the two the app was built
for, and only when a person's action in the UI triggers them: classifying an
uploaded photo, and phrasing a contents list into a record's summary.

**The line is between the app working and the assistant spending.** Starting
the app and exercising it -- `make run`, the local checks and CI targets,
uploading a photo, pressing "From contents" or "Look closer",
`scripts/claude/try_cloud_vision.py` (which drives the app's own endpoints) --
is the app doing its job, and is how the cloud path gets verified. What is
never sanctioned is a Claude session or subagent calling the API *itself*:
`curl`, the SDK, a REPL, or a script of its own, whether to check the key
works, benchmark a model, compare providers, or try a prompt. Route every real
call through the application's own code paths, and keep the spend to what a
person's use of the app would have cost.

Being unable to *read* the key is not the restriction; the restriction is on
*using* it, and it holds wherever a key is reachable. `.claude/settings.json`
denies reading `.env`, `security find-generic-password` and `op read`, which
says how the owner wants this handled. Build and prove everything against fakes
and `MOVING_VISION_PROVIDER=stub`; no test may make a network call.

## Where the real database lives

`var/moving.db` in the **main checkout**. `config.ROOT` follows the package, so
the CLI or a dev server run from a worktree silently uses that worktree's own
`var/`, which is deleted with it. B-0001 has a physical label in circulation;
do not lose its row. Back up (`uv run moving backup`) before anything
destructive.

## Where things live

| `src/movingbox/` | for |
|---|---|
| `cli.py`, `config.py` | the `moving` command; `Config`/`from_env`, home of every `MOVING_*` variable |
| `secrets.py` | `ANTHROPIC_API_KEY`: the environment, then this checkout's `.env`, then none |
| `db.py`, `migrations/` | connect and migrate (on every connect); numbered SQL |
| `store.py` | records, items, rooms, status, location, nesting; shared with the CLI |
| `storage.py`, `renditions.py` | photos on disk; the list thumbnail and the versioned strip image |
| `search.py` | the FTS5 index |
| `kinds.py`, `prefs.py`, `codes.py` | kinds, sizes, label copies; settings in the database; code format |
| `summarise.py`, `phrasing.py` | the assembled summary line; the model that phrases it, and its `Warmer` |
| `analysis.py`, `spend.py` | the background photo reader; cloud spend and the cap |
| `vision/` | `base` (schema, `parse`, `Reading`), `claude`, `ollama`, `hybrid`, `stub` |
| `ai.py` | `draft_for_box`: an on-demand draft that writes nothing |
| `labels/` | `layout.py`, `printer.py` (raster, backends, USB), `code128.py`, `fonts/Inter.ttf` |
| `api/` | `app.py` (factory, lifespan threads, `/health`, `/b/{code}`, `/sw.js`, `/api/events`), routers, `events.py`, `assets.py` |
| `export.py`, `manifest_pdf.py`, `backup.py`, `version.py` | data out; verified backups; the version number |

`web/`: `index.html` (shell, the only stylesheet, the icon sprite), `app.js`
(views and wiring), `sw.js`, and pure modules tested by `tests/*.test.mjs`:
`live.js` (event socket, when a refresh is safe, `reconcile`), `autosave.js`,
`reload.js`, `nesting.js`, `covers.js`, `segmented.js`, `wedge.js`, `scan.js`
(+ vendored `jsQR.js`), `text.js`. `scripts/claude/`: operational scripts with
purpose headers, the browser checks (`*_check.mjs`), `hooks/`, `lib/launchd.sh`.

## Constraints and traps

### HTTPS and the phone

- **HTTPS is not optional.** `getUserMedia` and `BarcodeDetector` are
  secure-context only and fail *silently* on a LAN IP (`localhost` is exempt).
  `tailscale serve` certifies `moving.example.ts.net`, which is also
  the base URL in every printed QR: changing it means reprinting.
- **The `/b/{code}` redirect must stay relative.** Tailscale proxies plain
  HTTP, so an absolute redirect built from the request says `http://` and drops
  the phone out of the secure context. Two tests guard it. uvicorn trusts
  `proxy_headers` from loopback only, so `request.url` reports https.
- **`serve` binds 127.0.0.1** (0.0.0.0 would add an unauthenticated LAN
  listener). **`tailscale serve`, never `tailscale funnel`.**
- **The phone is Firefox on Android**, in HTTPS-Only mode: never hand over an
  http URL. No `BarcodeDetector` there, so `scan.js` falls back to jsQR,
  vendored so scanning works offline; typing a code always works.
- MagicDNS does not resolve from the sandboxed tool shell:
  `curl --resolve moving.example.ts.net:443:$(tailscale ip -4) ...`.
- The websocket key rides in the query string (`/api/events?key=`), because a
  browser `WebSocket` cannot send `X-API-Key`, so it reaches uvicorn's access
  log. Acceptable only on a tailnet, usually with no `MOVING_API_KEY` set.

### Printer and labels

- **Geometry comes from `brother_ql.labels`:** label `62` (DK-2205, 62 mm
  endless), 696 printable dots at 300 dpi. Render exactly 696 px across.
- **Rotation belongs to the printer, not the layout.** `layout.render()` returns
  an image that reads normally (landscape 990 x 696); `printer.to_raster()`
  turns it. `build_instructions` rotates *before* its width check. The
  direction was settled on tape.
- **`brother_ql` is stale:** it warns `brother_ql.devicedependent is deprecated`
  on import (suppressed at its one import site), and its rescale path calls
  `PIL.Image.ANTIALIAS`, gone since Pillow 10 -- anything not 696 px wide dies
  with a misleading `AttributeError`, so `build_instructions` checks the width
  first. If it breaks further, swap in the GPL-3.0 fork `luxardolabs/brother_ql`
  behind the `PrinterBackend` protocol (one file).
- **Anything that opens the USB device must `usb.util.dispose_resources` it.**
  A handle leaked by the service holds the QL-800 exclusively and every print
  fails with a *permission* error; `ioreg -r -c IOUSBHostDevice -l | grep
  UsbExclusiveOwner` names the culprit pid. `sudo` does not fix it.
- All USB access goes through `printer.exclusive()`, or the threadpool
  interleaves two jobs' rasters. `AutoOffWatcher` turns the printer's auto
  power-off off once at startup (it persists in NVRAM), and does nothing on
  `fake`.
- The QL-800 (`0x04f9:0x209b`) has no CUPS queue: `brother_ql` over pyusb is the
  working backend. Editor Lite mode must be off.
- **Fonts are bundled** (`Inter.ttf`): golden-image tests compare bytes.
- **Tests decode with zbar, never `cv2.QRCodeDetector`** (which fails on valid
  codes). `tests/conftest.py` sets `DYLD_FALLBACK_LIBRARY_PATH` in `os.environ`
  before importing pyzbar so it finds Homebrew's libzbar (`DYLD_LIBRARY_PATH`
  would not work). A label has a QR and a Code 128: filter by symbol type.

### Server and database

- **Every route taking `get_conn` must be a sync `def`**, or the sqlite handle
  crosses threads and sqlite3 refuses it (`tests/test_api_structure.py`).
- **The change socket (`/api/events`) must stay database-free**: a websocket has
  to be `async def`. It carries an event kind and a box code; clients refetch
  over REST. Routes publish into the loop with `loop.call_soon_threadsafe`
  (`asyncio.Queue` is not thread-safe).
- **Writes carry `X-Client-Id`; events carry it back as `origin`**, and
  `live.js` `affects()` drops a device's own echo, which would redraw over
  half-typed text. Worker events have no `origin`, so every client acts on them.
- Mutations are announced by the route, not `store` (the CLI shares `store` from
  another process), so a CLI change is silent until the next refresh.
- **FTS is maintained explicitly**: call `search.reindex_box(conn, box_id)`
  after any change to a box, its items or photos. Input goes through
  `_match_expression`, which quotes every word -- FTS5 reads `-` as an operator.
- **Migrations are numbered SQL in `src/movingbox/migrations/`, against
  `PRAGMA user_version`. Never edit an applied one; add a new one.**
  `db.migrate()` runs on every connect, so the running service applies a new
  migration file on its next request.
- A brand-new database can 500 once (parallel first requests both migrate).
  Pre-migrate with `moving seed-rooms` before pointing a browser at it.
- Status and location are not in `store.EDITABLE`: their own calls log every
  transition. `schemas.Strict` makes a PATCH with an unknown field a 422.
- `ai_jobs.photo_id` has no foreign key (migration tests roll back by dropping
  columns), so `storage.delete_photo` deletes a photo's jobs itself.
- **`include_router` does not flatten into `app.routes`** here: each is an
  `_IncludedRouter` whose `routes` attribute is a *string*, with the real routes
  on `original_router`. A naive walk inspects nothing, silently.
- **Nothing in the suite can reach a model or the API**: `auto_analyse` and
  `phrase_summaries` are off and `vision_provider` is `"ollama"` in the `Config`
  dataclass (`from_env` turns them on), and `from_env({...})` reads no `.env`
  unless `MOVING_ENV_FILE` names one. Tests build `Config` directly.

### Photos and caches

- **A photo URL must name one set of bytes, forever.** `sw.js` is cache-first
  for everything but `/api/`, `/b/` and `/health`, and photos carry a year's
  `max-age`: even after a deploy wipes the worker's cache, the HTTP cache
  serves the old bytes (the "reload it twice" shape; `strip_cache_check.mjs`
  proves it). `/photos/{id}/thumb` and `/full` are safe only because their
  bytes never change; regenerating either needs a versioned URL first.
- The photo strip has its own 800 px sharpened image (the 400 px list
  thumbnail is untouched), at `/photos/{id}/strip?v=<renditions.VERSION>`, the
  version a hash of the recipe. **Any other version is a 404, never the current
  one**, which would be cached as the old one. `strip_name` reads the version
  per call.
- `moving thumbnails` only reads the database, never touches a full image or
  thumbnail, renames strips into place, and is idempotent; missing strips are
  also made on request. `--prune` is off by default: an old service may still
  advertise old strips.
- Photos are normalised on upload: 2048 px, EXIF orientation baked in, all
  other metadata stripped (GPS; the database gets exported), deduplicated by
  sha256 so retries are harmless.
- Measuring traps: under `srcset`, `naturalWidth` is density-corrected (decode
  `currentSrc` instead); a CDP screenshot `clip` is in document coordinates.

### Local models (Ollama)

- **Check what a tag is; small is not fast.** Every bare `qwen3-vl` tag, and
  `qwen3.5:2b`, is a *thinking* checkpoint: thousands of tokens of reasoning,
  no more accurate. The `-instruct` in the tag is what matters. With `format`
  set and thinking off, Ollama 0.34 puts the JSON in `message.thinking`, so
  `read_response` falls back to it.
- `keep_alive: "30m"` and `num_ctx: 8192` on every request: Ollama unloads
  after five idle minutes, and by default sizes context at 262k (25 GB for a 4b).
- **`OLLAMA_MAX_LOADED_MODELS` defaults to 3, and the app uses exactly three.**
  A fourth causes eviction thrash that looks like a slow model.
- `phrasing.Warmer` pings the summary model every 5 minutes, and **its
  `num_ctx` must match the real request's** (`phrasing.CONTEXT`, compared by a
  test) -- a different context reloads the model it just warmed.

### Deploying

- **Merging to `main` in the main checkout redeploys** (`hooks/post-merge` runs
  `deploy.sh --if-changed`; `MOVING_NO_DEPLOY=1` skips it).
- **Never deploy from a worktree.** Hooks live in the common git dir and fire in
  every worktree; the hook and `deploy.sh` compare `--absolute-git-dir` with
  `--git-common-dir` and bail.
- **Backup first, before the tests**: the old process applies a newly merged
  migration on its next request. Then tests gate: red aborts before launchd is
  touched, and the old build keeps serving.
- **`/health` reports `revision`, read once at startup** from
  `var/deployed-revision`; read per request it would claim the new commit
  whether or not the restart happened. `deploy.sh` fails on a mismatch.
- `MOVING_SERVICE_LABEL` / `MOVING_SERVICE_PORT` aim the scripts at a throwaway
  agent.
- **Cache-busting**: `/sw.js` gets its cache `VERSION` replaced by the deployed
  revision (never hand-bump it), and every asset URL carries `?v=<rev>`
  (`api/assets.py`) -- in `index.html`, the worker's shell list and every
  module's `import` lines. Current revision: `immutable`; anything else:
  `no-cache`. `MOVING_REVISION` names one for a throwaway server.
- **`/health` is `no-store` and bypasses the worker**: open pages poll it for
  deploys, and a cached answer hides the next one.
- **Open pages reload themselves after a deploy** (`web/reload.js`). The running
  revision is the page's own `?v=` (null in dev: nothing runs). It checks on
  socket reconnect, on becoming visible, and every 60 s while visible; a failed
  check means "cannot tell". `reloadBlocked` waits out an open dialog, a write
  in flight or a save still owed, showing "App updated -- tap to reload"; it
  never reloads on becoming hidden. It waits for the new worker to take over
  (`freshWorker`), and reloads once per revision per tab (`sessionStorage`).

### Shell, launchd and git

- **`CDPATH` is set in this user's shell**, so a relative `cd` prints its
  destination and `$(cd … && pwd)` returns two lines. Scripts in
  `scripts/claude/` clear it first; so must new ones.
- **`launchctl print` exits non-zero for an unloaded label** (fatal under
  `set -euo pipefail`); use `agent_pid` in `lib/launchd.sh`. Likewise `x && y`
  as a statement exits under `set -e` when `x` is false: use `if`.
- `reload_agent` exists once, in `lib/launchd.sh`: `launchctl bootout` is
  asynchronous, and bootstrapping too soon fails after unloading the service.
- The plist heredoc in `install-service.sh` is unquoted: escape backticks in it.
- **The version bumps itself.** `src/movingbox/version.py` is its only home.
  The pre-commit hook bumps the patch on every commit unless the commit stages
  `version.py` itself (that is how `make version-minor` works); otherwise leave
  the file to the hook. Branches conflict on that line: **take the higher, then let the
  merge commit bump it.** A clone without `make setup` silently stops bumping.

### Browser checks

- **Run `make browser-check` after touching `web/`.** A throwaway server (own
  database, fake printer, stub vision, `MOVING_REVISION` set) runs `ui_check`
  and `wedge_check` (read-only, assert they wrote nothing), `autosave_check`,
  `copies_check`, `viewer_check`, `nesting_check` (these write, and **refuse
  port 8787 and any non-loopback host**), then `strip_cache_check` and
  `reload_check` on servers of their own.
- **Never `pkill` Chrome by pattern** -- agents run in parallel and kill each
  other's. Give each Chrome its own debugging port and `--user-data-dir`
  (`make browser-check CHECK_PORT=8801 CDP_PORT=9366`). `--screenshot` with a
  debugging port never exits; `--window-size` does not narrow the layout (use
  `Emulation.setDeviceMetricsOverride`); headless Chrome follows the Mac's dark
  mode and fires no blur without focus emulation; `Page.navigate` to the open
  URL is a fragment navigation (use `Page.reload`).
- **`node --check file.js` proves nothing for an ES module.** Use
  `node --input-type=module --check < file`, as `tests/test_web_syntax.py` does.
- **`el.hidden` works only because of `[hidden] { display: none !important }`**
  in `index.html`: the UA's rule loses to any author `display`.

## Conventions

- **Printer backend defaults to `fake`**; tests force it in `tests/conftest.py`
  (pyproject's `env =` key is ignored without `pytest-env`).
- **Warnings are errors**; a third-party deprecation gets its own `ignore`.
- **A throwaway server is how write paths get checked**: `MOVING_DB_PATH`,
  `MOVING_PHOTO_DIR`, `MOVING_LABEL_PREVIEW_DIR`, `MOVING_BACKUP_DIR`,
  `MOVING_PRINTER_BACKEND=fake` (and `MOVING_VISION_PROVIDER=stub`) inline on
  the command, pre-migrated, on a spare port.
- `make lint` is ruff, black (line length 100), oxlint and stylelint (on the
  `<style>` block of `index.html`). `package.json` exists for the linters only.
  `currentColor` is lower-case in CSS but not in an SVG `fill` attribute.
- **Every `<form id>` drawn in `app.js` needs a submit listener**
  (`tests/test_web_forms.py`), or it submits natively and saves nothing.
- **`showError()` is for a view that could not be drawn; `failed()` for an
  action that failed on a page that is still good** (a dialog; the page, scroll
  and typed text stay).
- **Anything that hides or destroys asks first through `confirmed()`** (a native
  `<dialog>`, never `confirm()`; Cancel holds the focus). Removing one item from
  a list does not ask. **Every dialog uses `closesOnEscape`**: one Escape
  otherwise cancels every open dialog, not just the top one.
- **A live refresh is an interruption.** Rows update in place by code
  (`reconcile`, which sets `data-key`); rebuilt markup sends a tap to the wrong
  box. A refresh is held while a field is focused or dirty, a pointer is down,
  and for 600 ms after, raising the `#live` banner instead. An open record's
  items, photos and pristine summary still update in place (`partOf()`,
  `itemsPart`/`photosPart`), through the same code as the first draw.
- **The record page saves itself; there is no Save or Cancel** (`autosave.js`;
  `editSession` in `app.js`). Pickers save on change, text after 1.2 s and on
  blur, the current location only on blur or Return (each save is history). A
  save never redraws the page. One Undo stack, named for what it restores. A
  failed save says "Not saved yet" and retries; a 4xx does not. Leaving,
  hiding and `pagehide` commit; a whole-page redraw commits and **waits for the
  save to land** first. Code that sets a field tells the saver: an edit, or
  `track()` for a change from the server. One session per record (`sessions`),
  so a modal has its own Undo. The new-record form and Settings keep buttons.
- **One function per rule, so the first draw and a live update agree**:
  `rowStatus` (what a row is, then how far along; no status inside a
  container), `rowFor`/`fillRow`, `kindIcon`.
- **The app calls a record an "item"**; "box" is the kind. Beware:
  `kind = "item"` is one kind, and the things *in* a box are `items` in code.
  The package, `/api/boxes` and the tables were not renamed: the QR URLs
  (`/b/CODE`) on labels depend on them.
- **`destination_room_id` and `current_location` are different things**: a box
  for the kitchen may be on the truck right now. Conflating them breaks the
  main use case.
- AI output is provenance-tagged (`source='ai'`); `ai.draft_for_box` writes
  nothing.

## Decisions

Each is a choice; changing one is a decision, not the fixing of a gap.

### Labels (committed: a change means reprinting what is stuck to boxes)

- **Landscape, fixed 990 x 696 px** (3.3 in x 62 mm), identity only: number
  (160 px) with the QR flush top-right, a thin Code 128, the room band at
  `BAND_TOP` on every label, the summary (59 px), FRAGILE/HEAVY chips (72 px)
  on the bottom margin.
- **No dynamic font sizing**: same element, same size (`type_sizes()`); `_fit`
  only shrinks what physically cannot fit.
- **The QR's size is its module size** (`QR_MODULE`): `_qr` snaps to whole
  pixels, so a small target change may change nothing on tape.
- **OPEN FIRST is a double rule round the label**, inside `MARGIN`, not a chip.
  **The room band never moves.** Label icons are PIL polygons in `layout.py`.
- **Code 128 is hand-written** (`labels/code128.py`), the number only, for a
  wedge reader; zbar reads back every symbol value in the tests. It keeps a
  ten-module quiet zone or is skipped, never shrunk.
- **No contents column** (the list is one scan away): `LabelData` has no
  `items`; a box with items but no summary prints no description.
- **The stub** (`render_stub`, `stub` on print or preview): one inch (696 x
  300), number and QR, for a box still empty; exempt from the contents gate.
  Enter on the new-record form never prints.
- `orientation="portrait"` (cut to content, 300-1063 px) still works;
  `MOVING_LABEL_ORIENTATION` picks the default.
- **Printing refuses a box with no contents** (409 without `allow_empty`; one
  empty box rejects the batch); preview and the CLI are not gated. The UI asks
  about a thin label first (`confirmThinLabel`).
- **Copies are per kind** (`kinds.KINDS`, overridable in Settings): two for a
  box, tub or crate, which get stacked; one for the rest; one for a stub.

### Reading photos: Claude first, Ollama behind it

- **`claude-sonnet-5` reads every photo; `claude-opus-5` is "Look closer"**
  (`MOVING_VISION_CLOUD_MODEL`, `_CLOUD_DETAIL_MODEL`).
- **`qwen3-vl:4b-instruct` / `:8b-instruct` are the automatic fallback**
  (`MOVING_VISION_MODEL`, `_DETAIL_MODEL`): no key, refused, rate limited,
  unreachable or over budget all fall back in `vision/hybrid.py`, because on
  moving day the Mac rides in a van without internet. **No key is supported.**
- Why the cloud: the local 4b averaged 19.6 s a photo (92 s worst, 5 of 47
  errored); Sonnet, 4.3 s at $0.0082 ($2.50-8 for the move); a closer look ~$0.02.
- **Neither tier thinks** (`thinking: disabled`): measured on the closer look,
  the same readings and cost, and faster. The closer look's `effort: "medium"` is unmeasured.
- **The API is asked for the shape** (`output_config.format`); **`base.parse`
  is forgiving of the reply and strict about the outcome** -- no usable JSON
  raises `DraftUnreadable`, never an empty draft that reads as an empty box.
- **Photos go to the API at 1568 px** (its own limit) **and are stored at
  2048**: at 1024 the models stop reading small text and start inventing.
- **`base.Reading` says who answered and at what cost** ("Read by X" in the
  viewer); a paid call that could not be read is still charged to the job.
- **The cap is config, not a Settings field** (`MOVING_VISION_BUDGET_USD`,
  default 30), enforced per job (`spend.over_cap`), summed from `ai_jobs`.
  Unknown Claude models are priced at the dearest known rate.
- **A refused key looks exactly like a working one**; Settings -> Reading photos
  says when the last ten reads were all local.
- **The key**: `ANTHROPIC_API_KEY`, else this checkout's `.env`
  (`MOVING_ENV_FILE` names another), else none. `secrets.py` parses it (an
  unquoted value ends at ` #`). It never leaves the process: `repr=False`,
  `claude.redact()` on every message, and `/api/settings/spend` says only
  `key: true|false`.

### Photo analysis (`analysis.py`; the contract is in the spec)

- **What a photo shows is applied, not proposed** (photos only;
  `ai.draft_for_box` still proposes). The model's items are `source='ai'`; a
  person's items and summary (`boxes.summary_source`) are never changed;
  renaming an `ai` item makes it the person's. Neither field is client-settable.
- **One worker, one job at a time** (the model is the bottleneck); tests drive
  `Analyst.run_once()`. Jobs live in `ai_jobs`; `recover()` re-queues `running`.
- **Merging matches a normalised name**, crude on purpose: a missed merge is a
  deletable duplicate, a wrong one loses an item. Quantities only rise. A
  closer look adds, never removes.
- `MOVING_VISION_PROVIDER=stub` for UI work; `MOVING_AUTO_ANALYSE=0` stops it.

### Summaries

- **"From contents" fills the summary and saves at once**, with Undo;
  **`qwen2.5:7b`** (`MOVING_SUMMARY_MODEL`) phrases it as a category and
  examples -- the only candidate consistent in that shape, ~0.3 s.
- **Only the button phrases**; the photo worker assembles
  (`test_the_background_summary_is_never_written_by_a_model`).
- **The assembled line is the normal fallback**: under `phrasing.ENOUGH` (3)
  things, Ollama down, past `TIMEOUT` (5 s), no JSON; `source` says which.
  `MOVING_PHRASE_SUMMARIES=0` turns phrasing off.
- **A summary covers the whole subtree** (`store.subtree` ->
  `summarise.contents`): each descendant gives a person's summary, else its
  items, else an autogenerated summary, else what it is; at most 40 things.

### Things inside things

- One container per record, any depth (`boxes.parent_id`, `parent_code`). A
  container holding things cannot be deleted (409) or become a single thing;
  what is inside counts as contents.
- **Search looks everywhere** and draws each container above what was found in
  it (`matched`, `ancestry`, `groupMatches`), never losing or doubling a row.
- **A nested record goes where its nearest container with a room goes**
  (`inheritedRoom()`, `store.going_to()`).
- **Fragile climbs, never descends**: marking a nested thing fragile offers to
  mark its containers; clearing touches nothing else.
- **"Add something inside" is a dialog** (kind, photo, source room). Its live
  camera starts when it opens, checks `isSecureContext` first, and stops in
  the dialog's `close` handler and on `pagehide`.
- **Tapping something inside opens its editor as a modal** (`editSubitem`);
  closing commits and waits. Empty sections fold; nothing with content does.

### Kinds and input

- Kinds: box, parts, tub, crate, bag hold contents; loose item and furniture
  do not. Only a container has a size.
- **Kind, size and rooms are pushbutton rows** of native radios
  (`segmented.js`); an optional row clears on a second tap.
- **A barcode reader is a keyboard** (`wedge.js`): a scanned URL opens, a bare
  code is looked up then searched, with or without focus. The Return ending a
  scan is swallowed (it would press the last button -- print), "/" is
  suppressed mid-scan (Firefox quick find), and a focused radio is a button.

### Look

- **The PWA mirrors the printed label** (Inter, the code as hero, the black
  room band, true black), so a scan confirms it is the same object. The one
  departure is **`--radius: 4px`**, only through the token (a test checks).
- **The home mark** is the QR finder pattern with a house inside, on a 16-unit
  grid: 16, 24 or 32 px, never 28. The PWA icon is still a white bar.
- **Icons ship in three places only**: the nav bar (always with its word), an
  empty list thumbnail (the kind) and the three handling flags. The status
  track, nesting buttons, breadcrumb, headings, Delete, "Look closer" and the
  size row were tried and turned down (`docs/design/icons/NOTES.md`).
- Icon traps, all silent: `fill` goes on the referencing `<svg>`, not the
  sprite; a `<use>` of a missing id draws nothing (`tests/test_web_icons.py`);
  `createElement("svg")` draws nothing (use `createElementNS`).

## Closed decisions -- do not re-propose

- **No pre-printed blank label batches**: create and print one at a time.
- **No offline write sync**: the PWA needs the tailnet reachable.
- **No DK-2251 black and red**: the renderer is mono; the knockout band sorts.
- **No insurance valuation report, no multi-user accounts.**
- *"No cloud vision provider" was on this list and was reversed on 2026-09-20*,
  on measurement: see "Reading photos" above.

## Known limitations -- real, not decisions

- `cups_raw` is unusable: the QL-800 registers no CUPS queue. USB works; this
  matters only if macOS ever claims the device.
- The phone UI for photos and drafting is verified by API tests and headless
  browser checks, but has not been exercised on a real handset.
- Nor has the live-update client: the server half is tested through a real
  uvicorn/websockets stack and `live.js`'s decisions directly, but
  `LiveChannel`'s wiring to a browser `WebSocket`, and the tap-target behaviour
  it protects, have not been watched on a handset.

Remote: **github.com/rhooper/moving** (private). Push after merging to main.
