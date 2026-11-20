"""The eBay description built from a Unit's grades and notes."""

from fbc.ebay import describe
from fbc.models.inventory import Unit


def _unit(**fields):
    return Unit(name="Carnaval", **fields)


def test_it_says_each_grade_and_note_then_the_footer():
    unit = _unit(
        sleeve_grade="VG+",
        sleeve_note="Light ring wear.",
        dust_jacket_grade="NM or M-",
        dust_jacket_note="Original, clean.",
        record_grade="VG+",
        record_note="Plays great.",
        extras="Includes the poster.",
        pressing="US Original",
        unit_type='12" LP',
        matrix="MCA-5108-A1",
    )
    html = describe.build_description(unit, ["Ships in a mailer."])
    assert html == (
        "<h3>Condition</h3><ul>"
        "<li><b>Sleeve:</b> Very Good Plus (VG+). Light ring wear.</li>"
        "<li><b>Dust Jacket:</b> Near Mint (NM or M-). Original, clean.</li>"
        "<li><b>Record:</b> Very Good Plus (VG+). Plays great.</li></ul>"
        "<p>Includes the poster.</p>"
        "<h3>The record</h3><ul><li><b>Pressing:</b> US Original</li>"
        '<li><b>Format:</b> 12" LP</li><li><b>Matrix:</b> MCA-5108-A1</li></ul>'
        "<p>Ships in a mailer.</p>"
    )


def test_a_part_with_nothing_to_say_is_left_out():
    html = describe.build_description(
        _unit(record_grade="VG", record_note="Some noise."), []
    )
    assert (
        html
        == "<h3>Condition</h3><ul><li><b>Record:</b> Very Good (VG). Some noise.</li></ul>"
    )
    assert "Dust Jacket" not in html and "The record" not in html


def test_values_are_escaped():
    html = describe.build_description(
        _unit(record_note="<script>x</script> & more"), []
    )
    assert "<script>" not in html
    assert "&lt;script&gt;x&lt;/script&gt; &amp; more" in html


def test_without_notes_the_old_description_is_used_as_it_was():
    unit = _unit(record_grade="VG+", description="Cover looks great.")
    assert describe.build_description(unit) == "<p>Cover looks great.</p>"


def test_notes_win_over_the_old_description():
    unit = _unit(record_note="Plays great.", description="Old text.")
    assert "Old text." not in describe.build_description(unit, [])


def test_the_footer_defaults_to_the_built_in_line():
    html = describe.build_description(_unit(record_note="Plays great."))
    assert html.endswith("<p>All records are ultrasonically cleaned.</p>")


def test_an_empty_footer_says_nothing():
    assert describe.build_description(_unit(record_note="Fine."), []).endswith("</ul>")


def test_a_unit_with_nothing_at_all_gives_only_the_footer():
    assert describe.build_description(_unit(), []) == ""
    assert describe.build_description(_unit()) == (
        "<p>All records are ultrasonically cleaned.</p>"
    )
