# Moving Box Tracker

[![CI](https://github.com/rhooper/moving/actions/workflows/ci.yml/badge.svg)](https://github.com/rhooper/moving/actions/workflows/ci.yml)

Track what went into which box during a house move, print QR labels on a Brother
QL label printer, and scan them with a phone to find things again.

It answers two questions:

- **What is in this box?** Scan the label, or search for an item by name.
- **Where is the thing I need right now?** Every record has a current location
  (the truck, garage stack 3) separate from the room it is going to, because
  boxes sit in staging piles for days.

A record can be a box, a parts box, a tub, a crate, a bag, a loose item or a
piece of furniture, and containers can hold other records -- a bag in a box in
a crate. Search looks inside all of them. Photograph an open box and a vision
model lists what is in it for you.

It is a small self-hosted web service (FastAPI + SQLite) and a phone web app (a
PWA, plain ES modules, no build step). It was built for one household's move
and runs on a single Mac; there are no user accounts.

## Requirements

| | Needed for | Notes |
|---|---|---|
| **Python 3.13+** and **[uv](https://docs.astral.sh/uv/)** | everything | uv installs the Python dependencies from `uv.lock` |
| **HTTPS to the server** | the phone camera and scanner | [Tailscale](https://tailscale.com/) `serve` is the tested route; see below |
| Brother QL label printer + DK-2205 tape | printing labels | tested on the **QL-800** over USB; needs `libusb` (`brew install libusb`) |
| fribidi (`brew install fribidi`) | label typography | without it Pillow falls back to basic text layout: labels still print, without Inter's kerning |
| [Ollama](https://ollama.com/) | reading photos offline, phrasing summaries | optional; ~14 GB of models |
| Anthropic API key | reading photos with Claude | optional; about a cent a photo |
| Node.js 22+ | development only: the JS/CSS linters and JS tests | |
| zbar (`brew install zbar`) | development only: the label tests decode QR codes | |

**Operating system.** The app runs anywhere Python does. The service installer
and deploy scripts (`scripts/claude/install-service.sh`, `deploy.sh`) use
launchd and are macOS-only; on Linux, run `uv run moving serve` under systemd
or similar. The test suite's label goldens are pinned to macOS font rendering.

Without a printer, labels render to PNG files (`var/labels/preview/`). Without
Ollama or a key, everything works except reading photos.

## Installation

```bash
git clone https://github.com/rhooper/moving.git
cd moving
uv sync                          # Python dependencies into .venv
cp moving.example.toml moving.toml   # then edit it: see Configuration
uv run moving seed-rooms         # a starter set of rooms (also creates the database)
uv run moving serve              # http://127.0.0.1:8787
```

Open `http://localhost:8787` on the same machine: `localhost` counts as secure,
so everything works there. For a phone, set up HTTPS next.

For development, `make setup` also installs the linters (npm) and the git hooks,
and `make run` starts a reloading dev server on :8788. `make help` lists the
rest.

### HTTPS is mandatory for the phone

The camera and the scanner (`getUserMedia`, `BarcodeDetector`) only work in a
secure context. Over `http://192.168.x.x` they fail silently: no scanning, no
photos, no install prompt. `localhost` is exempt; a LAN address is not.

Tailscale issues a real certificate for each machine on your tailnet:

```bash
tailscale serve --bg 8787    # https://<machine>.<tailnet>.ts.net -> 127.0.0.1:8787
```

Use `serve`, never `funnel`: the app has no accounts, and `serve` keeps it to
your own devices. Put that address in `moving.toml` as `base_url` **before
printing any labels** -- it is encoded in every QR code, so changing it later
means reprinting. (The in-app scanner reads the code from a `/b/` URL on any
host; only the phone camera's tap-through would break.) A real printer refuses
to print until `base_url` is set.

### Running it permanently (macOS)

```bash
scripts/claude/install-service.sh              # --uninstall to remove
```

That installs a launchd agent on 127.0.0.1:8787 (starts at login, restarts if it
dies) with the `brother_ql` printer backend, a nightly backup, and the git
hooks, and points `tailscale serve` at it.

### Photo reading models

```bash
cp .env.example .env               # then put ANTHROPIC_API_KEY in it
chmod 600 .env

ollama pull qwen3-vl:4b-instruct   # the local reader: 3.3 GB
ollama pull qwen3-vl:8b-instruct   # its closer look: 6.1 GB
ollama pull qwen2.5:7b             # phrases "From contents" summaries: 4.7 GB
```

Any of these can be left out; see Configuration to choose the engine.

## Configuration

Settings live in **`moving.toml`** in the checkout (copy `moving.example.toml`,
which lists every key with its default). Every key is optional. Each also has a
`MOVING_*` environment variable, named beside it in the example, and **a
variable wins over the file**. `MOVING_CONFIG=/path/to/file.toml` reads a
different file. A misspelt section or key stops startup with its name rather
than being ignored.

The API key is the one thing that does not go in `moving.toml`: it goes in
`.env` (or the environment as `ANTHROPIC_API_KEY`), so the settings file can be
shared.

### Choosing the LLM engine

```toml
[vision]
provider = "claude"   # "claude", "ollama" or "stub"
```

| `provider` | Reads photos with | Network |
|---|---|---|
| `"claude"` (default) | Claude first; the local Ollama model whenever Claude cannot -- no key, offline, rate limited, refused, or over budget | Anthropic API |
| `"ollama"` | the local model only | none leaves the machine |
| `"stub"` | a canned answer after `stub_seconds`, for UI work | none |

`"claude"` with no key is a supported setup: it simply reads everything
locally. The other engine settings:

```toml
[vision]
auto_analyse = true                        # read each photo as it is uploaded
budget_usd = 30.0                          # lifetime cloud spend cap; past it, photos are read locally
cloud_model = "claude-sonnet-5"            # reads every photo
cloud_detail_model = "claude-opus-5"       # "Look closer" in the photo viewer
local_model = "qwen3-vl:4b-instruct"       # the Ollama fallback
local_detail_model = "qwen3-vl:8b-instruct"

[summary]
phrase = true                              # "From contents" asks a model to phrase the line
model = "qwen2.5:7b"                       # an Ollama model

[ollama]
url = "http://localhost:11434"             # Ollama on another machine works too
```

With a local vision model, keep the `-instruct` tags: the bare `qwen3-vl` tags
are *thinking* checkpoints, several times slower and no more accurate.

### Everything else

| Section | Keys |
|---|---|
| `[server]` | `base_url` (printed in every QR), `api_key` (require `X-API-Key`; unset is open) |
| `[storage]` | `db_path`, `photo_dir`, `label_preview_dir`, `backup_dir` -- relative to the file; default `var/` |
| `[printer]` | `backend` (`fake`, `brother_ql`, `cups_raw`), `model`, `queue`, `label`, `orientation` |

## Using it

### Scanning

The phone's own camera reads a label and opens the record -- no app needed. The
app's Scan view is faster for many boxes in a row. On Firefox and Safari it
uses a bundled QR reader, so it works offline too; you can always type a code.

A USB barcode reader works too: scan a label into the search box, or with
nothing selected, and the record opens.

### Labels

62 mm continuous DK-2205 tape, black only. A label is a fixed 3.3 inches along
the tape and carries identity, not an inventory: the box number, a QR code, a
barcode of the number, the destination room knocked out white on a black band
(what you read across a room of stacked boxes), a one-line summary, and
FRAGILE / HEAVY marks. "Open first" is a double border round the whole label.
The full contents list is one scan away.

- **A label will not print for a box with nothing recorded in it.** An empty
  box with a label is indistinguishable from an unlabelled one until opened.
  The app asks before printing a label with no contents or no destination.
  Previewing is never gated, and neither is the command line.
- **The stub** is one inch of tape with just the number and QR, for a box you
  have only just started.
- **Copies**: two labels for a box, tub or crate (they get stacked and more
  than one side shows), one for everything else. Change it per kind in
  Settings.

The printer backend defaults to `fake`, which writes a PNG to
`var/labels/preview/` instead of using tape.

```bash
uv run moving preview B-0001                    # PNG only
uv run moving print B-0001 --backend brother_ql # over USB
uv run moving print B-0001 --orientation portrait   # the older cut-to-fit form
```

Turn **Editor Lite mode off** on the printer, or it presents as a disk and
ignores print jobs. `cups_raw` needs `[printer] queue`, and refuses to run
without it rather than sending a raster to whichever printer is the default.

### Photos that list the contents for you

Photograph the open box before taping it -- the camera button beside Edit on
any record takes one there and then. Each photo is read in the background
and what is in it is added to the contents list, marked *autogenerated*; you
carry on packing while it works. **Look closer** (in the photo viewer) asks
the more careful model for a second read, better at handwriting and brand
names.

**What it costs** with Claude: under a cent and a half a photo, about two cents
for a closer look: a move of 300-1,000 photos is a few dollars. Settings ->
**Reading photos** shows the running total against the cap and which model is
reading your photos right now.

What it will and will not touch:

- Anything **you** typed is left alone: your items are never renamed,
  re-counted or removed, and a summary you wrote is never overwritten.
- A second photo of the same things adds nothing; a better count raises a
  quantity, never lowers one.
- Tap an autogenerated name to correct it. From then on it is yours.

**From contents** writes the one-line summary for you, from everything in the
box and everything inside the things in it: "Kitchen essentials - a stock pot,
baking pans, and a stand mixer". It saves at once; Undo puts the old one back.

Photos are downscaled to 2048 px and **all metadata is stripped** -- indoor
photos carry GPS, and this database gets exported. Uploading the same photo
twice is harmless.

### Backups

```bash
uv run moving backup        # verified, keeps the last 14
```

The macOS installer adds a nightly backup at 03:17. Backups use SQLite's online
backup API rather than a file copy (the service holds the database open), are
verified before older ones are pruned, and are single files you can open
read-only.

### Getting the data out

```bash
uv run moving export --format json -o moving.json
uv run moving export --format csv  -o moving.csv
uv run moving manifest                     # counts and weight per room
curl -O https://<host>/api/manifest.pdf    # for the movers
```

Exports name rooms rather than ids and nest items inside their box, so they
stand alone. The manifest's weight total says how many boxes it covers, since
not every box is weighed.

## Development

```bash
make setup           # uv sync, npm ci (linters only), git hooks
make check           # lint + tests (no printer, model or network needed)
make browser-check   # headless-Chrome checks against a throwaway server (~2 min)
```

The tests force the fake printer and the stub vision provider and read no
`moving.toml` or `.env`, so they never print, never call a model and never
spend. CI runs lint on Ubuntu and the tests on macOS.

**Deploying (the author's setup).** In a checkout installed with
`install-service.sh`, merging to `main` redeploys: the `post-merge` hook runs
`scripts/claude/deploy.sh`, which refuses a worktree, another branch or a dirty
tree; backs up the database; runs the test suite (a red test stops it and the
old build keeps serving); restarts the service; and checks that `/health` names
the new commit. Open pages reload themselves once nothing is being edited.
`MOVING_NO_DEPLOY=1 git merge …` skips it once.

| Path | Contents |
|---|---|
| `src/movingbox/` | FastAPI service, label rendering, vision providers |
| `src/movingbox/migrations/` | numbered SQL, applied against `PRAGMA user_version` |
| `web/` | the PWA -- plain ES modules, no build step |
| `scripts/claude/` | install, deploy, backup and check scripts; `hooks/` holds the git hooks |
| `docs/` | the photo-analysis API contract and the icon design record |
| `var/` | database, photos, label previews; gitignored, backed up separately |

Working notes for developers, and the reasons behind the decisions, are in
`CLAUDE.md`.

## License

MIT (`LICENSE`). Bundled fonts and scripts, and one GPL dependency, are
listed in `THIRD_PARTY_NOTICES.md`.
