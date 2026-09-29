# Configuration

Every setting, where it can be set, and what wins. The short version is in
`README.md`; `moving.example.toml` is a copy-and-edit template of the file.

## Where a value comes from

For each setting, first match wins:

1. **The environment**, as a `MOVING_*` variable.
2. **The settings file**: `moving.toml` in the checkout, or the file
   `MOVING_CONFIG` names.
3. **The built-in default** (below).

The file is strict: an unknown section, an unknown key or a wrong type stops
startup with a `ConfigError` naming it, rather than being ignored. Relative
paths in the file are relative to the file; relative paths in a variable are
relative to the working directory. A boolean variable is off for `0`, `false`,
`no` or `off`, and on for anything else.

Git ignores `moving.toml`, `.env` and `var/`, so none of an install's own
address, key or data can be committed by accident.

## The settings file

### `[server]`

| Key | Variable | Default | Meaning |
|---|---|---|---|
| `base_url` | `MOVING_BASE_URL` | `https://moving.example` | The HTTPS address phones reach the app on. **Printed into every label's QR code**, so changing it means reprinting. The default is a placeholder: every printer backend except `fake` refuses to print until it is set. |
| `api_key` | `MOVING_API_KEY` | unset | When set, every API request must carry it in `X-API-Key` (the websocket takes it as `?key=`). Unset is open, which suits a private tailnet. |

### `[storage]`

| Key | Variable | Default | Meaning |
|---|---|---|---|
| `db_path` | `MOVING_DB_PATH` | `var/moving.db` | The SQLite database. Migrated on every connection. |
| `photo_dir` | `MOVING_PHOTO_DIR` | `var/photos` | Uploaded photos and their renditions. |
| `label_preview_dir` | `MOVING_LABEL_PREVIEW_DIR` | `var/labels/preview` | Where the `fake` printer and `moving preview` write label PNGs. |
| `backup_dir` | `MOVING_BACKUP_DIR` | `var/backups` | `moving backup` output; the newest 14 are kept. |

The defaults are under the checkout's own `var/`. A worktree has its own
`var/`, deleted with it.

### `[printer]`

| Key | Variable | Default | Meaning |
|---|---|---|---|
| `backend` | `MOVING_PRINTER_BACKEND` | `fake` | `fake` writes a PNG preview and reports success; `brother_ql` prints over USB; `cups_raw` sends to a CUPS queue (the QL-800 registers none). |
| `model` | `MOVING_PRINTER_MODEL` | `QL-800` | The `brother_ql` model name. |
| `queue` | `MOVING_PRINTER_QUEUE` | unset | The CUPS queue, for `cups_raw` only. |
| `label` | `MOVING_LABEL_ID` | `62` | The `brother_ql` label id: 62 mm endless tape (DK-2205). The layout is drawn for this width. |
| `orientation` | `MOVING_LABEL_ORIENTATION` | `landscape` | `landscape` (fixed 990 x 696) or `portrait` (cut to content). |

### `[vision]`: who reads uploaded photos

| Key | Variable | Default | Meaning |
|---|---|---|---|
| `provider` | `MOVING_VISION_PROVIDER` | `claude` | `claude`: Claude first, the local Ollama model whenever Claude cannot answer (no key, offline, rate limited, refused, over budget). `ollama`: local only, nothing leaves the machine. `stub`: a canned answer after `stub_seconds`, for UI work (it also stubs summary phrasing). |
| `auto_analyse` | `MOVING_AUTO_ANALYSE` | `true` | Read each photo in the background as it is uploaded. |
| `budget_usd` | `MOVING_VISION_BUDGET_USD` | `30.0` | Lifetime cloud spend cap, summed from recorded jobs. Past it, photos are read locally. |
| `cloud_model` | `MOVING_VISION_CLOUD_MODEL` | `claude-sonnet-5` | Reads every photo. |
| `cloud_detail_model` | `MOVING_VISION_CLOUD_DETAIL_MODEL` | `claude-opus-5` | "Look closer" in the photo viewer. |
| `local_model` | `MOVING_VISION_MODEL` | `qwen3-vl:4b-instruct` | The Ollama model, and the stand-in for `cloud_model`. |
| `local_detail_model` | `MOVING_VISION_DETAIL_MODEL` | `qwen3-vl:8b-instruct` | The Ollama stand-in for `cloud_detail_model`. |
| `stub_seconds` | `MOVING_VISION_STUB_SECONDS` | `3.0` | How long the `stub` provider takes. |

Keep the `-instruct` tags on local models: the bare `qwen3-vl` tags are
*thinking* checkpoints, several times slower and no more accurate. A Claude
model the app has no price for is charged against the budget at the dearest
known rate.

### `[summary]`: "From contents"

| Key | Variable | Default | Meaning |
|---|---|---|---|
| `phrase` | `MOVING_PHRASE_SUMMARIES` | `true` | Ask a local model to phrase the summary line. Off, or with Ollama unreachable, the line is assembled from the contents instead. |
| `model` | `MOVING_SUMMARY_MODEL` | `qwen2.5:7b` | The Ollama model that phrases it. |

### `[ollama]`

| Key | Variable | Default | Meaning |
|---|---|---|---|
| `url` | `MOVING_OLLAMA_URL` | `http://localhost:11434` | Ollama's address; another machine works. |

The app keeps three models loaded (vision, detail, summary), which is Ollama's
default `OLLAMA_MAX_LOADED_MODELS`. Lower it and the models evict each other,
which looks like a slow model.

## The Anthropic API key

Never in `moving.toml`, so the settings file can be shared. First match wins:

1. `ANTHROPIC_API_KEY` in the environment.
2. `ANTHROPIC_API_KEY=...` in `.env` in the checkout, or in the file
   `MOVING_ENV_FILE` names. `KEY=VALUE` lines, `#` comments, optional quotes
   and `export`; an unquoted value ends at ` #`. See `.env.example`.
3. None, which is a working setup: every photo is read locally.

The app warns at startup if `.env` is readable by other users (`chmod 600
.env`). The key is never logged, served, stored or put in an error message;
`/api/settings/spend` reports only whether one is present. A refused key looks
exactly like a working one from the outside, so Settings -> Reading photos says
when the last ten reads were all local.

## Environment only

| Variable | Used by | Meaning |
|---|---|---|
| `MOVING_CONFIG` | the app, the CLI | Read this settings file instead of the checkout's `moving.toml`. |
| `MOVING_ENV_FILE` | the app, the CLI | Read the API key from this file instead of the checkout's `.env`. |
| `MOVING_REVISION` | the app | The revision to report in `/health` and put in asset URLs, instead of `var/deployed-revision`. For throwaway servers. |
| `MOVING_SERVICE_LABEL` | `install-service.sh`, `deploy.sh`, the post-merge hook | The launchd label. Default `local.movingbox`. |
| `MOVING_SERVICE_PORT` | `install-service.sh`, `deploy.sh` | The installed service's port. Default `8787`. A label or port other than the default installs a second instance and leaves `tailscale serve` alone. |
| `MOVING_NO_DEPLOY` | the post-merge hook | `1` skips the redeploy after a merge to `main`. |

## The installed service

`scripts/claude/install-service.sh` writes a launchd agent that runs `moving
serve` on `127.0.0.1:8787` from the checkout, and **sets
`MOVING_PRINTER_BACKEND` in the agent's environment** (to `brother_ql`, or to
whatever `MOVING_PRINTER_BACKEND` was when you ran the script). Because the
environment wins, `[printer] backend` in `moving.toml` has no effect on the
installed service: re-run the script to change it. Everything else comes from
`moving.toml` as usual.

## Make variables

| Variable | Default | For |
|---|---|---|
| `PORT` | `8788` | `make run`'s dev server (the installed service owns 8787). |
| `CHECK_PORT` | `8797` | The throwaway server `make browser-check` starts. |
| `CDP_PORT` | `9340` | Chrome's debugging port for the browser checks. Give parallel runs their own `CHECK_PORT` and `CDP_PORT`. |
| `CODES` | none | `make proof CODES="B-0003 ..."` adds real records to the label proof sheet. |
