"""Where the Anthropic API key comes from, and where it must never go.

Resolution order, most specific first:

1. ``ANTHROPIC_API_KEY`` in the environment -- what a throwaway server, a
   one-off script or a test uses, and what must keep working.
2. The **login keychain**, item ``moving-anthropic`` / account ``moving``,
   read through ``/usr/bin/security``. The owner puts it there once, in their
   own terminal (never through a session, which would put the literal key in a
   transcript):

       security add-generic-password -s moving-anthropic -a moving -w

   Omitting the value makes it prompt without echoing.
3. **No key at all, which is not an error.** The hybrid falls back to the local
   model, so an absent key degrades the *reading* rather than breaking the app.
   Somebody restarting the service before they have authenticated gets working
   local analysis, not an outage. This is the design, not an error path.

Deliberately **not** the launchd plist: ``launchctl print`` renders a service's
environment, so a variable there is readable by anything that can run
launchctl. ``install-service.sh`` knows nothing about the key and its heredoc
is left alone.

The value is read once, at startup, into `Config`. It is never logged, never
put in an exception message (`vision.claude.redact` scrubs any that a reply
quotes back), never announced as an event, never served by a route, and never
written to the database. `Config` keeps it out of its own `repr` so a traceback
cannot spill it into var/log.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping

#: The keychain item. Generic password, the owner's login keychain.
SERVICE = "moving-anthropic"
ACCOUNT = "moving"
SECURITY = "/usr/bin/security"

#: `security` can put up a keychain-access prompt, and this runs under launchd
#: where nobody is there to answer it. A service that hangs at startup is worse
#: than one reading photos locally, so the wait is short and a timeout is
#: simply "no key".
TIMEOUT = 5.0


def from_keychain(
    *,
    service: str = SERVICE,
    account: str = ACCOUNT,
    timeout: float = TIMEOUT,
    run: Callable[..., object] = subprocess.run,
) -> str | None:
    """The stored key, or None. Never raises, never hangs, never prompts twice.

    `run` is injected so the suite can exercise this without touching the real
    keychain -- a test that read it would be a test that could spend money.
    """
    try:
        done = run(
            [SECURITY, "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # No `security` binary, no keychain, or a prompt nobody answered.
        return None
    if getattr(done, "returncode", 1) != 0:
        return None
    # stdout is the secret and nothing else; stderr is deliberately not read,
    # because anything that went wrong is "no key" and quoting it risks
    # putting part of the item in a log.
    return (getattr(done, "stdout", "") or "").strip() or None


def anthropic_api_key(
    env: Mapping[str, str],
    *,
    lookup: Callable[[], str | None] = from_keychain,
) -> str | None:
    """The key for this process, or None."""
    named = (env.get("ANTHROPIC_API_KEY") or "").strip()
    if named:
        return named
    return lookup()
