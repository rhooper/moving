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
cp moving.example.toml moving.toml  # settings: LLM engine, printer, base_url (env vars win)
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
| `cli.py`, `config.py` | the `moving` command; `Config`/`from_env`, home of every `MOVING_*` variable and its `moving.toml` key (`FILE_KEYS`) |
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
`reload.js`, `nesting.js`, `covers.js`, `record.js` (the read-only sheet),
`camera.js` (the live camera's rules), `segmented.js`, `wedge.js`, `scan.js`
(+ vendored `jsQR.js`), `text.js`. `scripts/claude/`: operational scripts with
purpose headers, the browser checks (`*_check.mjs`), `hooks/`, `lib/launchd.sh`.

## Constraints and traps

### HTTPS and the phone

- **HTTPS is not optional.** `getUserMedia` and `BarcodeDetector` are
  secure-context only and fail *silently* on a LAN IP (`localhost` is exempt).
  `tailscale serve` certifies this Mac's `<machine>.<tailnet>.ts.net`, which is
  also the base URL in every printed QR: changing it means reprinting. The real
  address lives only in the main checkout's `moving.toml` (`[server] base_url`),
  never in tracked files: the repository is meant to be public.
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
  `curl --resolve <host>:443:$(tailscale ip -4) ...`, the host from `moving.toml`.
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
  `conftest.py` also points `MOVING_CONFIG` at nothing, since the real
  `moving.toml` names the real database and printer.

### Photos and caches

- **A photo URL must name one set of bytes, forever.** `sw.js` is cache-first
  for everything but `/api/`, `/b/` and `/health`, and photos carry a year's
  `max-age`: even after a deploy wipes the worker's cache, the HTTP cache
  serves the old bytes (the "reload it twice" shape; `strip_cache_check.mjs`
  proves it). `/photos/{id}/thumb` and `/full` are safe only because their
  bytes never change; regenerating either needs a versioned URL first.
- **An id is not enough to name bytes: every photo URL carries `k`**
  (`renditions.key`, the first 12 of the upload's sha256; `photo.key`,
  `cover_photo_key`; `photoUrl`/`thumbUrl` in `covers.js`), and a key that is
  not the photo's is a 404. Until migration 0011 (AUTOINCREMENT), deleting the
  newest photo gave its id to the next one taken, and a phone showed B-0057's
  new photo as the one deleted from B-0056 out of its year-long cache. Ids
  are now never reused, but ids reused before then are covered only by `k`.
  **Build photo URLs from id and key, never the id alone.** A URL with no key
  is still served, for pages open across a deploy.
- `boxes` and `items` still use a bare `INTEGER PRIMARY KEY`, so their ids can
  be reused the same way. No URL is cached by them, but a stale request from
  another device aimed at a deleted item's id could reach a new one.
- The photo strip has its own 800 px sharpened image (the 400 px list
  thumbnail is untouched), at `/photos/{id}/strip?v=<renditions.VERSION>`, the
  version a hash of the recipe. **Any other version is a 404, never the current
  one**, which would be cached as the old one. `strip_name` reads the version
  per call.
- `moving thumbnails` only reads the database, never touches a full image or
  thumbnail, renames strips into place, and is idempotent; missing strips are
  also made on request. `--prune` is off by default: an old service may still
  advertise old strips.
- Photos are normalised on upload: **2048 px on the short edge, within 4096 on
  the long** (`storage.kept_size`, and `frameSize` in the live camera, which
  must agree), EXIF orientation baked in, all other metadata stripped (GPS; the
  database gets exported), deduplicated by sha256 so retries are harmless. Kept
  larger than any model reads, so a person can zoom into a drawer label.
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
  `copies_check`, `viewer_check`, `nesting_check`, `newbox_check` (which also
  covers the sheet's camera, since it already has the fake webcam; these write,
  and **refuse port 8787 and any non-loopback host**), then `strip_cache_check`
  and `reload_check` on servers of their own.
- **Never `pkill` Chrome by pattern** -- agents run in parallel and kill each
  other's. Give each Chrome its own debugging port and `--user-data-dir`
  (`make browser-check CHECK_PORT=8801 CDP_PORT=9366`). `--screenshot` with a
  debugging port never exits; `--window-size` does not narrow the layout (use
  `Emulation.setDeviceMetricsOverride`); headless Chrome follows the Mac's dark
  mode and fires no blur without focus emulation; `Page.navigate` to the open
  URL is a fragment navigation (use `Page.reload`). For the camera,
  `Browser.grantPermissions` takes CDP's own enum, and
  `Browser.resetPermissions` is how to refuse.
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

### Settings and the public release

- **`moving.toml` is the settings file** (asked for as "make it easy to
  configure the llm engine from a config file"; TOML over INI for typed values
  and stdlib `tomllib`). `moving.example.toml` documents every key and is
  tested against `FILE_KEYS` and the real defaults. **A `MOVING_*` variable
  wins over the file**, so throwaway servers and the plist still override one
  setting. `read_config_file` turns the file into those variables, so there is
  one parser. **Strict**: an unknown section or key, or a wrong type, is a
  `ConfigError` at startup. `MOVING_CONFIG` names another file; relative paths
  are relative to the file.
- **The Anthropic key has no key in `moving.toml`**: it stays in `.env`, so the
  settings file can be shared.
- **`DEFAULT_BASE_URL` is a placeholder** (`https://moving.example`), and
  `Config.unprintable()` makes any backend but `fake` refuse it (503 from the
  API, exit 1 from the CLI): a QR pointing nowhere cannot be taken back. The
  live install's real address is in the main checkout's `moving.toml`.
- **MIT**, chosen by the owner. `THIRD_PARTY_NOTICES.md` covers Inter (OFL),
  jsQR (Apache 2.0) and `brother_ql` (GPLv3+, installed, not bundled).
- **CI** (`.github/workflows/ci.yml`): lint on Ubuntu, tests on **macOS**,
  because the label goldens compare bytes and Linux FreeType renders Inter a
  pixel row differently. The browser checks are not in CI.
  `astral-sh/setup-uv` has no floating major tag: pin the exact version. On
  failure the job uploads the goldens as rendered there (`rendered-labels`).
- **The goldens need raqm, which needs libfribidi at run time.** Without it
  Pillow silently uses its basic layout -- no kerning, and Inter's hyphen in
  "B-0042" sits lower -- and every golden differs all over.
  `test_text_is_shaped_as_the_goldens_were` says so first. Pillow dlopens it
  by bare name, and dyld reads `DYLD_FALLBACK_LIBRARY_PATH` at process start,
  so `conftest.py` setting it is too late: CI and the launchd plist set it
  outside the process (`/opt/homebrew/lib`).
- **Portable shell**: `sed -i.bak` + `rm`, never `sed -i ''` (BSD-only; the
  version hook failed on Linux).
- The git history still contains the tailnet hostname and absolute home paths
  from before the scrub; rewriting it was not done.

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
  300) for a box still empty -- the number, a Code 128 of it beneath, and the
  QR; exempt from the contents gate. The barcode is **skipped, not shrunk**,
  when it cannot keep a ten-module quiet zone clear of the QR, and the number
  then centres rather than sitting high over empty tape -- so a long code
  loses the barcode and keeps the QR. **Two symbols on it now**, so a test
  that decodes a stub must filter by symbol type, as the full label's already
  must. It counts in `label_print_count` like any label -- the purge warning
  is about anything scannable stuck to a box.
- **Making a record prints nothing** ("default to no stub label"): plain Create
  is the first, filled button on the new-record form and the stub is the quiet
  one beside it, one press away. Enter reaches Create by arrangement now, and
  the handler that points it there stays -- it is also what stops a barcode
  reader's Return submitting from a focused pushbutton.
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
  errored); Sonnet, 4.3 s at $0.0082 when photos were sent at 1568 -- now
  ~$0.013 for a new photo sent at 1658x2212 ($4-13 for the move).
- **Neither tier thinks** (`thinking: disabled`): measured on the closer look,
  the same readings and cost, and faster. The closer look's `effort: "medium"` is unmeasured.
- **The API is asked for the shape** (`output_config.format`); **`base.parse`
  is forgiving of the reply and strict about the outcome** -- no usable JSON
  raises `DraftUnreadable`, never an empty draft that reads as an empty box.
- **Each model is sent its own copy, sized for it.** The cloud tier fits the
  API's *high-resolution* tier (Claude 4.7 and later): at most 2576 px on the
  long edge and 4,784 visual tokens, one token per 28x28 patch
  (`claude.fitted`, checked against the API docs' own table). The old 1568 px
  cap is the *standard* tier's, and cost the model half its pixels. The local
  model gets 2048 on the long edge (`ollama.for_local`), where it was measured
  reading small text within its 8192-token context; it is never sent the
  larger kept file. At 1024 the models stop reading small text and invent.
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
  **`qwen2.5:7b`** (`MOVING_SUMMARY_MODEL`) phrases it, ~0.4 s.
- **The line leads with the KINDS of things, examples in support** ("Music
  tech: interfaces, pedals, sequencers, CDs, with an APC mini"), asked for as
  "prefer a high level view of the kinds of things in the box, not the exact
  items". Median 87 characters against 64 for the old list-of-items shape.
  The stated length in the prompt steers it ("under 200" gave 64, "about 90"
  gives 87) but is **not obeyed as a ceiling** -- one answer came back at 147
  -- so `_tidy` enforces `MAX_LENGTH`.
- **`temperature` is 0, and that is what buys consistency.** Over seven real
  records, three runs each, it takes identical answers from 5 of 7 records to
  7 of 7. *This corrects an earlier note here*: a previous attempt at a more
  categorical prompt was abandoned for "costing consistency", and the same
  prompt at temperature 0 is perfectly consistent. The variation was sampling,
  not the wording. What is still true is that a **genuinely mixed container
  gets a wrong category** -- three unrelated things came back as "Home decor"
  -- which is why `ENOUGH` is where it is.
- **Only the button phrases**; the photo worker assembles
  (`test_the_background_summary_is_never_written_by_a_model`).
- **The assembled line is the normal fallback**: under `phrasing.ENOUGH` (4)
  things, Ollama down, past `TIMEOUT` (5 s), no JSON; `source` says which.
  `MOVING_PHRASE_SUMMARIES=0` turns phrasing off. **Four is measured against
  the tape**: up to three distinct things the assembled list still fits the
  two lines a label with a room band and a handling chip leaves, so there is
  nothing to summarise -- and that is exactly the band where the model invents
  a category.
- **`summarise.MAX_LENGTH` (100) is set by the label, not chosen.** The
  renderer wraps the summary and keeps only the lines that fit above the
  handling chips -- three with a room band, two if there is also a chip --
  so the ceiling is *lines*. Rendered on real text at the label's own font and
  width: 3 lines hold about 100 characters, 2 lines about 65. The old 240 was
  never the binding limit; the tape was, and anything longer was cut with an
  ellipsis. Cutting at 100 instead lets the assembled line end on "and N
  more", which says how much is missing. The kinds-first shape also truncates
  well: on a two-line label "Music tech: interfaces, pedals, sequencers, CDs,
  with an APC mini..." still says what the box is. Check any change to this by
  rendering (`make proof`, `CODES="B-0015 ..."`), not by picking a number.
- **A summary covers the whole subtree** (`store.subtree` ->
  `summarise.contents`): each descendant gives a person's summary, else its
  items, else an autogenerated summary, else what it is; a container that
  holds something says nothing of itself, and the record's own summary is
  never read back in. At most `MAX_THINGS` (40), nearest first.

### Reading a record

- **`#/b/CODE` reads; `#/b/CODE/edit` is the page that saves itself.** Asked
  for as "a view mode with an edit button for viewing boxes. make it compact,
  and gather photos and subitem covers near each other, near the top":
  somebody standing over a sealed box wants to know what it is, and every
  control on the editor writes the moment it is touched. **The editor is
  unchanged** -- the route table's `[^/]+` cannot match "CODE/edit", so it is
  one added row.
- **The sheet, top to bottom**: the way out (nested only), the code with Edit,
  the room band, the summary, any raised flags, then **one field of pictures**
  -- the record's own photographs in a scrolling row and the covers of what is
  inside it as tiles captioned with their codes, because both answer "what is
  this?" and only what is inside can be opened -- then four label-and-value
  lines, then Print label and Add something inside. The code's row carries the
  camera beside Edit. The rules are `record.js`,
  tested: `coverTiles` (eight cells, the last the way in to the rest, because
  B-0015 holds twenty tubs), `contentsLine`, `facts`, `expandedGroups`.
- **Nothing on it edits.** No `editSession`, so no field holds a live refresh
  and there is nothing to commit on leaving, and the page draws no input at
  all -- `ui_check` asserts that, and that a whole visit writes nothing. Print,
  Add something inside and Restore are deliberate presses that ask first;
  Restore is there because a scanned binned label lands here.
- **The camera beside Edit is the one press on it that writes** (`#take-photo`
  -> `takePhoto`, asked for as "a camera icon in a button next to edit ... a
  popup with the live camera feature"): somebody standing over a box should not
  have to reach the editor to photograph it. It is the shared camera field in a
  `<dialog>` appended to `<body>`, so the page still draws no input, and it
  starts the camera on open -- pressing a camera button *is* asking for one.
  **Taking is adding** ("automatically add it on take. i can delete them
  after"): there is no Add button; the shutter, or a chosen file, uploads at
  once and the viewfinder comes straight back for the next, and Done closes it.
  A wrong shot is deleted from the editor, which is the trade asked for.
  **Not drawn on a binned record**: `save_photo` does not see the bin (404, a
  test pins it), and Restore is the honest press there. A failed upload keeps
  the still, says why, and shows Try again (`field.resend()`); the server keeps
  one copy of the same bytes, so a retry after an upload that did land adds
  nothing.
- **The dialog keeps nothing from the draw it was opened on.** A live refresh
  redraws the sheet underneath it (`editableFields()` reads `#app` only, so a
  dialog in `<body>` holds nothing back), so it asks `requestPart("photos")`,
  which reaches whatever draw is current, and returns the focus to the button
  by id rather than to a node it kept.
- **A scan lands on the sheet**: `openEntered`, `scan.js` and the server's
  `/b/{code}` all use the bare hash, so none of them changed. Every link to
  *another* record opens the sheet; the three paths that **make** a record
  (Add and open, the line naming what was just added, the new-record form) and
  "Open its whole page" in the sub-item modal go to the editor.
- **It watches as `name: "box"`**, which is what `affects()` knows -- a new
  view name matches no event and fails silently. Tiles and facts redraw in
  place through `reconcile`, which sets the `data-key` an update finds them by;
  without one an update draws the sheet twice.
- **Where it is *right now* is its own line**, and only when there is one; the
  `What` line comes from `rowStatus`, so a list row and the sheet cannot drift.
- **The whole contents list is a separate request** (`GET
  /api/boxes/{code}/contents` -> `summarise.grouped_items` over
  `store.subtree`), made when the line is opened: B-0015 is 59 items of its own
  plus twenty tubs' worth, and carrying that on every record GET would slow
  opening any record for a list most people never open. Each record's items are
  merged and sorted **within** that record and never across them -- the same
  name in a crate and in a tub inside it is two things in two places, which is
  what the code heading exists to say. The normaliser is `summarise.name_key`,
  moved there out of `analysis` so there is one rule (`summarise._merge`, which
  the printed line uses, still matches on case alone: merging plurals there
  would change what prints).

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
- **"Add something inside" is a dialog** (kind, photo, source room), and its
  camera is the shared one below.
- **Tapping something inside opens its editor as a modal** (`editSubitem`);
  closing commits and waits. Empty sections fold; nothing with content does.

### Taking a photograph before there is a record

- **One camera, for the three places that take one**: the "Add something
  inside" dialog, the new-record page (asked for as "add a live camera to the
  new box page - optional image") and the record sheet's camera dialog. Only
  the last photographs a record that already exists, so it passes
  `photoField({onTaken})`: each photo is sent the moment it is taken, and
  `photoLine` says "adding", "added (N so far)" or "unsent" instead of "once
  the record exists".
  `photoField` in `app.js` builds the field --
  viewfinder, shutter, the frame just taken with Take another, the file picker
  underneath, the line -- and `web/camera.js` holds the decisions:
  `CAMERA_REQUEST` (asking for a size, or the browser gives 640x480),
  `frameSize` (what the server keeps), `cameraTrouble`, `streamQuality`,
  `viewfinderState`, `photoLine`. A second copy for a second page is how two
  cameras start drifting apart.
- **`isSecureContext` is checked before asking**: on a plain LAN address
  `getUserMedia` rejects with nothing that explains itself.
- **Every failure is ordinary** -- refused, none there, one already in use, an
  insecure address: a line says which, the box is put away rather than left as
  a dead grey rectangle, and the file picker underneath still works. The picker
  has no `capture` attribute on purpose: the live camera is the camera now.
- **The photo is optional everywhere it appears**, and never stands between
  anyone and the buttons. The record is created first and the photograph
  uploaded to it after, so a photo that will not upload leaves the record
  standing and says so.
- **A dialog stops its tracks on `close`; a page has no such event.** The
  new-record page registers its camera with `holdOnPage`, and `route()` lets go
  of it before drawing the next view (beside `leaveEveryRecord()`), with
  `pagehide` for the tab going away. A track left running keeps the camera
  light on and drains a phone carried round a house. `newbox_check` reads the
  track back after leaving the page and requires `ended`.
- **The page starts a camera only where one is already allowed**
  (`navigator.permissions.query({name: "camera"})`, which Firefox does not know
  that name for and so answers "no"); otherwise **"Use the camera"** sits beside
  "Choose a photo" and one press brings the viewfinder up. Opening New is not
  asking for a camera, and a phone that declined once must not be asked again
  on every visit. **A dialog still starts one when it opens** -- taking a photo
  is its point, and the sheet's dialog was opened by pressing a camera -- but
  never at page load.
- **A declined camera cost a later save, measured.** With the page asking and
  being denied, `autosave_check` lost the `pagehide` + `keepalive` save of the
  last edit before leaving the site: 4 runs failed with the camera started and
  denied, 2 passed with it not started, 1 passed with it granted and running,
  all against one server. Headless Chrome's own state after a denied permission
  may be the whole of it; asking for nothing nobody allowed avoids it either
  way.
- **An `await` in the middle of a view leaves its form unwired**: asking the
  permission before the submit listener was attached let a fast press submit
  natively. `startIfAllowed` runs last and is never awaited.
- **Two of an id in one document is a silent wrong click**: the field's button
  was `#adder-open`, which the dialog already uses for "Add and open". Ids
  inside the field are prefixed per call site for exactly this reason.

### Kinds and input

- Kinds: box, parts, tub, crate, bag hold contents; loose item and furniture
  do not. Only a container has a size. Known: undoing a kind change does not
  bring back the size the server cleared.
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
- **Icons ship in four places only**: the nav bar (always with its word), an
  empty list thumbnail (the kind), the three handling flags, and the record
  sheet's camera button. The status track, nesting buttons, breadcrumb,
  headings, Delete, "Look closer" and the size row were tried and turned down
  (`docs/design/icons/NOTES.md`).
- **The camera button is the one mark that ships without a word**, so it
  carries its name as `aria-label` and `title` (a test checks), at 24 px in a
  44 px quiet square beside a filled Edit. It is the exception to "no icons on
  buttons", not a precedent: four of the six turned-down cases were buttons.
- **`docs/design/icons/work/icons.py` is the one source** of `set/*.svg` and
  `sprite.svg`; `web/index.html` inlines the sprite verbatim. Add a mark there
  and run it -- `parts` was added to the sprite by hand and the next run
  dropped it.
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
