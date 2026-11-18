"""Every table in the Reports has alternating row colours, for easier reading."""

import re
from pathlib import Path

REPORTS = Path(__file__).resolve().parent.parent / "fbc" / "templates" / "reports"


def test_every_report_table_is_striped():
    unstriped = []
    for path in sorted(REPORTS.glob("*.html")):
        for tag in re.findall(r"<table\b[^>]*>", path.read_text()):
            classes = re.search(r'class="([^"]*)"', tag)
            if not classes or "table-striped" not in classes.group(1).split():
                unstriped.append(path.name + ": " + tag)
    assert unstriped == []


def test_the_reports_have_tables_to_check():
    # Guards the test above: if the templates move, it must not pass on nothing.
    tables = sum(
        len(re.findall(r"<table\b", p.read_text())) for p in REPORTS.glob("*.html")
    )
    assert tables >= 10
