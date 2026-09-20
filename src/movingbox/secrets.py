"""The `.env` file: where the Anthropic API key comes from, and where it must
never go.

Two sources, most specific first:

1. ``ANTHROPIC_API_KEY`` in the environment -- a test, a throwaway server, or
   anyone who would rather export it by hand.
2. **`.env` in the project root**, parsed for ``ANTHROPIC_API_KEY=...``.
3. Neither, which is **not an error**: the hybrid falls back to the local
   model, so an absent key degrades the *reading* rather than breaking the
   app. Somebody who starts the service before putting the key in place gets
   working local analysis, not an outage. This is the design, not an error
   path, and it is the most important line in this module.

`.env` rather than a keychain, a plist variable or a secret manager, because
the hazard list for this project is short and `.env` is already off all of it:
`.gitignore` lists it under "Local secrets", so it cannot be committed by
accident; backups use SQLite's online backup API against the database alone
and never sweep the working tree; and `export.py` writes database contents,
not files.

The grammar is `KEY=VALUE`, `#` comments and blank lines, with surrounding
quotes stripped -- parsed here rather than by adding python-dotenv, because
this project keeps a deliberately thin dependency list and that is the whole
of it. Keys the app does not know are ignored, since the file will grow other
settings.

The value is read **once, at startup**, into `Config`. It is never logged,
never put in an exception message (`vision.claude.redact` scrubs any that a
reply quotes back), never announced as an event, never served by a route, and
never written to the database. `Config` keeps it out of its own `repr` so a
traceback cannot spill it into var/log. Nothing here ever reports *part* of a
value: a malformed file says the file is malformed, and a rejected key says
the key was rejected.
"""

from __future__ import annotations

import logging
import stat
from collections.abc import Mapping
from pathlib import Path

log = logging.getLogger(__name__)

#: Names the app reads out of the file. Anything else is somebody else's
#: setting and is left alone rather than warned about.
KNOWN = ("ANTHROPIC_API_KEY",)


def parse(text: str) -> dict[str, str]:
    """`KEY=VALUE` lines, `#` comments, blank lines, optional quotes.

    A line that is not one of those is skipped rather than fatal: half a file
    of settings should not stop the app, and the one thing that matters --
    whether there is a key -- answers itself.
    """
    found: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        name, sep, value = line.partition("=")
        name = name.strip()
        if not sep or not name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            # A quoted value keeps whatever is inside the quotes, `#` included.
            value = value[1:-1]
        else:
            # An unquoted value ends at a trailing comment. This matters more
            # than it looks: an API key with " # mine" still on the end is
            # sent verbatim and comes back as a 401, which reads as a bad key
            # rather than as a bad line.
            value = value.split(" #", 1)[0].split("\t#", 1)[0].strip()
        found[name] = value
    return found


def read(path: Path) -> dict[str, str]:
    """Settings from the file at `path`. An absent file is simply no settings."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}
    except OSError as unreadable:
        # Named, never quoted: the reason can mention the path, not the file.
        log.warning("could not read %s: %s", path, unreadable.strerror)
        return {}

    try:
        mode = path.stat().st_mode
    except OSError:
        mode = 0
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        # Not fatal -- refusing to start over a file mode would be worse than
        # the exposure -- but nobody notices this until it matters.
        log.warning("%s is readable by other users; run: chmod 600 %s", path, path)

    return parse(text)


def anthropic_api_key(env: Mapping[str, str], *, env_file: Path | None) -> str | None:
    """The key for this process, or None. None is a working configuration."""
    named = (env.get("ANTHROPIC_API_KEY") or "").strip()
    if named:
        return named
    if env_file is None:
        return None
    return (read(env_file).get("ANTHROPIC_API_KEY") or "").strip() or None
