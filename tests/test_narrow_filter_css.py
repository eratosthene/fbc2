"""The CSS that stops list filters being cut off on a phone.

This reads the stylesheet as text. It can't say how the filters look: that
needs a browser at phone width.
"""

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "fbc/static/css/fbc.css").read_text()


def narrow_block():
    start = CSS.index("@media screen and (max-width: 767px)")
    return CSS[start:]


def test_filter_table_is_stacked_and_not_clipped_under_768px():
    block = narrow_block()
    assert re.search(r"table\.filters\s*\{[^}]*overflow:\s*visible", block)
    assert re.search(
        r"table\.filters\s*>\s*tbody\s*>\s*tr\s*>\s*td[^{]*\{[^}]*display:\s*block",
        block,
    )
    assert "select2-container" in block
    assert ".select2-dropdown" in block and "min-width" in block


def test_rules_only_touch_the_filter_form():
    # The list tables must keep their sideways scroll.
    block = narrow_block()
    assert not re.search(r"(^|\n)\s*\.table-responsive", block)
