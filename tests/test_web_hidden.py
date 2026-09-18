"""The `hidden` attribute must actually hide things.

The UA stylesheet's `[hidden] { display: none }` loses to any author display
rule, so `el.hidden = true` on an element styled `display: flex` does nothing.
That shipped for real: the nav printer badge showed "Printer offline" forever
while the printer sat there online. The stylesheet carries an author-level
override; this pins it.
"""

import re
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "web" / "index.html"


def test_the_stylesheet_overrides_display_rules_for_hidden_elements():
    assert re.search(
        r"\[hidden\]\s*\{\s*display:\s*none\s*!important", INDEX.read_text()
    ), "index.html lost its [hidden] { display: none !important } rule"
