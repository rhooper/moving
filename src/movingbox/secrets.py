"""Where the Anthropic API key comes from, and where it must never go.

Sources, most specific first: ``ANTHROPIC_API_KEY`` in the environment, then
``.env`` in the project root. Having neither is a working configuration, not
an error: the hybrid reads photos locally.

The value is read once, at startup, into `Config` (kept out of its `repr`). It
is never logged, never put in an exception message (`vision.claude.redact`
scrubs any a reply quotes back), never sent as an event, never served by a
route and never written to the database. Nothing reports *part* of a value.
"""

from __future__ import annotations

import logging
import stat
from collections.abc import Callable, Mapping
from pathlib import Path

log = logging.getLogger(__name__)

#: Names the app reads out of the file; others are left alone.
KNOWN = ("ANTHROPIC_API_KEY",)


def parse(text: str) -> dict[str, str]:
    """`KEY=VALUE` lines, `#` comments, blank lines, optional quotes. Other lines are skipped."""
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
            # An unquoted value ends at a trailing comment; a key sent with
            # " # mine" on the end comes back as a 401 that reads as a bad key.
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
        # The path and the reason, never the contents.
        log.warning("could not read %s: %s", path, unreadable.strerror)
        return {}

    try:
        mode = path.stat().st_mode
    except OSError:
        mode = 0
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        # A warning, not a refusal to start.
        log.warning("%s is readable by other users; run: chmod 600 %s", path, path)

    return parse(text)


def lookup(env: Mapping[str, str], *, env_file: Path | None) -> Callable[[str], str | None]:
    """A plug-in's key lookup: `env` first, then `env_file`, else None.

    The file is read once, now, as the Anthropic key is. What comes back holds
    the values, so it goes only where `repr=False` keeps it out of sight.
    """
    named = dict(env)
    in_file = read(env_file) if env_file is not None else {}

    def key(name: str) -> str | None:
        return (named.get(name) or "").strip() or (in_file.get(name) or "").strip() or None

    return key


def anthropic_api_key(env: Mapping[str, str], *, env_file: Path | None) -> str | None:
    """The key for this process, or None. None is a working configuration."""
    named = (env.get("ANTHROPIC_API_KEY") or "").strip()
    if named:
        return named
    if env_file is None:
        return None
    return (read(env_file).get("ANTHROPIC_API_KEY") or "").strip() or None
