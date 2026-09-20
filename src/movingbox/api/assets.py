"""Serve the PWA's files with the deployed revision in every asset URL.

There is no build step to stamp filenames, so cache-busting happens as files
are served. `/app.js` is referred to as `/app.js?v=<revision>` everywhere --
in index.html, in the service worker's shell list, and in the `import` lines
*inside* each module, because a versioned entry point that imports unversioned
modules busts nothing. A URL carrying the current revision is cached forever;
anything else, index.html above all, is revalidated every time.

The hole this closes: on a new deploy the service worker re-fetched its shell,
and the browser's HTTP cache could answer with last week's app.js -- which is
why a deploy used to need "reload it twice".

A dev checkout has no deployed revision ("unknown"): files are served exactly
as written, and nothing is cached, so an edit shows up on the next reload.
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from fastapi.responses import Response

#: A year, and never revalidated: the revision in the URL is what changes.
FOREVER = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"

#: A same-origin asset path, as it appears quoted or inside url( ).
#:
#: - one path segment only: `/app.js`, never `/photos/1/full` or `/api/...`
#: - the extensions the shell is made of, so `href="#/settings"` and the API's
#:   paths cannot match
#: - **not sw.js**: a service worker is identified by its script URL, and
#:   versioning that would register a new worker per deploy instead of updating
#:   the one there is
_ASSET = re.compile(
    r"""(?P<open>["'(])(?P<path>/(?!sw\.js)[\w.-]+\.(?:js|ttf|png|webmanifest))(?P<close>["')])"""
)

#: What may be rewritten: text that *refers* to other assets.
_REWRITTEN = {".html", ".js"}


def versioned(text: str, revision: str) -> str:
    """`text` with every asset reference carrying `?v=revision`."""
    if revision == "unknown":
        return text
    return _ASSET.sub(lambda m: f"{m['open']}{m['path']}?v={revision}{m['close']}", text)


def cache_header(asked_for: str | None, revision: str) -> str:
    """Forever for the current revision's URL, revalidate for anything else.

    "Anything else" includes last week's `?v=`: an old tab asking for an old
    URL gets this week's bytes, and must not pin them under the old name.
    """
    if revision != "unknown" and asked_for == revision:
        return FOREVER
    return REVALIDATE


#: Where the page shows the running version; filled in as it is served, so it
#: is right without a request and cannot go stale under a cached page.
_VERSION_SLOT = '<span id="version" class="version"></span>'


def stamped(html: str, version: str) -> str:
    return html.replace(_VERSION_SLOT, f'<span id="version" class="version">v{version}</span>', 1)


def respond(
    path: Path, *, asked_for: str | None, revision: str, version: str | None = None
) -> Response:
    """One file from the web root, rewritten if it refers to others."""
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if path.suffix == ".js":
        media_type = "application/javascript"
    elif path.suffix == ".webmanifest":
        media_type = "application/manifest+json"

    headers = {"Cache-Control": cache_header(asked_for, revision)}
    if path.suffix in _REWRITTEN:
        text = versioned(path.read_text(), revision)
        if version and path.suffix == ".html":
            text = stamped(text, version)
        return Response(text, media_type=media_type, headers=headers)
    return Response(path.read_bytes(), media_type=media_type, headers=headers)
