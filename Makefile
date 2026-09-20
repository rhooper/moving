# Moving Box Tracker -- a thin front door over uv and scripts/claude/.
#
# There is no build step: Python runs from source under uv, and the PWA is
# plain ES modules. "Building" is `make setup`; everything else is running it.
#
# The live service (launchd agent ca.toybox.moving) owns port 8787, so the dev
# server defaults to 8788 and the two never fight over the socket.

PORT ?= 8788
# The throwaway server and Chrome debugging port the browser checks use.
# Override both when two runs must not collide (agents run these in parallel).
CHECK_PORT ?= 8797
CDP_PORT ?= 9340


.DEFAULT_GOAL := help
.PHONY: help setup run test lint fmt check ui-check browser-check proof version version-minor version-major deploy install uninstall status labels backup

help:  ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

setup:  ## install dependencies: Python (uv), the JS/CSS linters (npm), and the git hooks
	uv sync
	npm ci --no-audit --no-fund
	scripts/claude/install-hooks.sh >/dev/null

run: setup  ## dev server with reload on :8788 (make run PORT=xxxx to change)
	uv run moving serve --port $(PORT) --reload


test:  ## run the test suite (printer forced to fake)
	uv run pytest -q

lint:  ## ruff + black (Python), oxlint (JS), stylelint (the CSS in index.html)
	uv run ruff check src tests scripts
	uv run black --check -q src tests scripts
	npm run -s lint:js
	npm run -s lint:css

fmt:  ## reformat Python with black
	uv run black -q src tests scripts

version:  ## show the version (the patch bumps itself on every commit)
	@sed -n 's/^__version__ = "\(.*\)"$$/\1/p' src/movingbox/version.py

version-minor:  ## x.Y+1.0 -- stage it; the commit hook leaves a hand-made bump alone
	@scripts/claude/bump.sh minor

version-major:  ## X+1.0.0
	@scripts/claude/bump.sh major

check: lint test  ## lint + test

ui-check:  ## real clicks and real barcode-reader keystrokes in headless Chrome (needs `make run` going)
	node scripts/claude/ui_check.mjs http://127.0.0.1:$(PORT)
	node scripts/claude/wedge_check.mjs http://127.0.0.1:$(PORT)

deploy:  ## backup, test, restart the live service, verify its revision
	scripts/claude/deploy.sh

install:  ## install the launchd agent + tailscale serve (persistent https)
	scripts/claude/install-service.sh

uninstall:  ## remove the launchd agent
	scripts/claude/install-service.sh --uninstall

status:  ## health and printer state of the live service
	@curl -s http://127.0.0.1:8787/health; echo
	@curl -s http://127.0.0.1:8787/api/printer; echo

browser-check:  ## EVERY browser check, writing ones included, on a throwaway server (~2 min)
	PORT=$(CHECK_PORT) CDP_PORT=$(CDP_PORT) scripts/claude/browser_checks.sh

proof:  ## test print without tape: label proof sheet, inline in iTerm (CODES="B-0003 ..." adds real records)
	uv run python scripts/claude/label_proof.py $(CODES)

labels:  ## render a contact sheet of sample labels to eyeball
	uv run python scripts/claude/render_samples.py

backup:  ## verified database backup + prune
	uv run moving backup
