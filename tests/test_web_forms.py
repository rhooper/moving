"""Every form the PWA draws must have its submit handled in JS.

A <form> with no submit listener still submits -- natively. The browser
reloads the page with the fields in the query string and saves nothing, which
looks exactly like a save that silently failed. That shipped: the box page's
summary form was drawn, its handler never landed, and "Save summary" did
nothing for as long as it existed. There is no JS linter here, so this is the
guard.
"""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "web" / "app.js"

FORM_ID = re.compile(r'<form[^>]*\bid="([^"]+)"')


def form_ids(source: str) -> list[str]:
    return sorted(set(FORM_ID.findall(source)))


def test_the_scan_finds_the_forms_it_is_meant_to_guard():
    # A guard that silently matches nothing guards nothing.
    assert "summary-form" in form_ids(APP.read_text())


def test_every_form_has_a_submit_listener():
    source = APP.read_text()
    unhandled = []
    for form_id in form_ids(source):
        # getElementById("x") ... .addEventListener("submit" -- via a variable
        # or chained directly; either way both must exist for this id.
        fetched = re.search(
            rf'(?:const|let)\s+(\w+)\s*=\s*document\.getElementById\("{re.escape(form_id)}"\)',
            source,
        )
        chained = re.search(
            rf'getElementById\("{re.escape(form_id)}"\)\??\.addEventListener\("submit"',
            source,
        )
        via_variable = fetched and re.search(
            rf'\b{fetched.group(1)}\??\.addEventListener\("submit"', source
        )
        if not (chained or via_variable):
            unhandled.append(form_id)
    assert not unhandled, f"forms drawn but never handled (native submit reloads the page): {unhandled}"
