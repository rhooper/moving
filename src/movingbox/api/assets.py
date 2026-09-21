"""Serve the PWA's files with the deployed revision in every asset URL.

With no build step, cache-busting happens as files are served: `/app.js`
becomes `/app.js?v=<revision>` in index.html, in the service worker's shell
list, and in the `import` lines inside each module -- a versioned entry point
importing unversioned modules busts nothing. The current revision's URLs are
cached forever; anything else is revalidated. In dev (revision "unknown")
files are served as written.
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from fastapi.responses import Response

#: A year, and never revalidated: the revision in the URL is what changes.
FOREVER = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"

#: A same-origin, single-segment asset path, quoted or inside url( ). Never
#: sw.js: a worker is identified by its script URL, so versioning it would
#: register a new worker per deploy instead of updating the one there is.
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

    An old `?v=` gets the current bytes, which must not be pinned under the old name.
    """
    if revision != "unknown" and asked_for == revision:
        return FOREVER
    return REVALIDATE


#: Where the page shows the running version, filled in as it is served.
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
