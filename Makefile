# Moving Box Tracker -- a thin front door over uv and scripts/claude/.
#
# There is no build step: Python runs from source under uv, and the PWA is
# plain ES modules. "Building" is `make setup`; everything else is running it.
#
# The live service (launchd agent ca.toybox.moving) owns port 8787, so the dev
# server defaults to 8788 and the two never fight over the socket.

PORT ?= 8788

.DEFAULT_GOAL := help
.PHONY: help setup run test lint check ui-check browser-check proof deploy install uninstall status labels backup

help:  ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

setup:  ## install dependencies (uv sync)
	uv sync

run: setup  ## dev server with reload on :8788 (make run PORT=xxxx to change)
	uv run moving serve --port $(PORT) --reload

test:  ## run the test suite (printer forced to fake)
	uv run pytest -q

lint:  ## ruff over src, tests and scripts
	uv run ruff check src tests scripts

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
	scripts/claude/browser_checks.sh

proof:  ## test print without tape: label proof sheet, inline in iTerm (CODES="B-0003 ..." adds real records)
	uv run python scripts/claude/label_proof.py $(CODES)

labels:  ## render a contact sheet of sample labels to eyeball
	uv run python scripts/claude/render_samples.py

backup:  ## verified database backup + prune
	uv run moving backup
