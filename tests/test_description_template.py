"""The eBay description template: its marks, the editor page, and the Draft."""

import pytest

import fbc
from fbc.ebay import describe, template
from fbc.ebay.template import TemplateError
from fbc.models.inventory import Unit
from fbc.models.templates import DescriptionTemplate


def _unit(**fields):
    return Unit(name="Carnaval", **fields)


def test_the_built_in_template_says_each_grade_and_note_then_the_footer():
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
    assert describe.build_description(unit) == (
        "<h3>Condition</h3><ul>"
        "<li><b>Sleeve:</b> Very Good Plus (VG+). Light ring wear.</li>"
        "<li><b>Dust Jacket:</b> Near Mint (NM or M-). Original, clean.</li>"
        "<li><b>Record:</b> Very Good Plus (VG+). Plays great.</li></ul>"
        "<p>Includes the poster.</p>"
        "<h3>The record</h3><ul><li><b>Pressing:</b> US Original</li>"
        '<li><b>Format:</b> 12" LP</li><li><b>Matrix:</b> MCA-5108-A1</li></ul>'
        "<p>All records are ultrasonically cleaned.</p>"
    )


def test_a_part_with_nothing_to_say_is_left_out():
    html = describe.build_description(_unit(record_grade="VG", record_note="Noisy."))
    assert "<li><b>Record:</b> Very Good (VG). Noisy.</li>" in html
    assert (
        "Dust Jacket" not in html and "The record" not in html and "<p>Inc" not in html
    )


def test_values_are_escaped():
    html = describe.build_description(_unit(record_note="<script>x</script> & more"))
    assert "<script>" not in html
    assert "&lt;script&gt;x&lt;/script&gt; &amp; more" in html


def test_without_notes_the_old_description_is_used_as_it_was():
    unit = _unit(record_grade="VG+", description="Cover looks great.")
    assert describe.build_description(unit) == "<p>Cover looks great.</p>"


def test_notes_win_over_the_old_description():
    unit = _unit(record_note="Plays great.", description="Old text.")
    assert "Old text." not in describe.build_description(unit)


def test_a_unit_with_nothing_at_all_gives_only_the_footer():
    assert describe.build_description(_unit()) == (
        "<p>All records are ultrasonically cleaned.</p>"
    )


def test_marks_and_if_blocks():
    unit = _unit(record_note="Fine.", extras="")
    body = "<p>{record_note}</p>{if extras}<i>{extras}</i>{end}{if extras or record_note}!{end}"
    assert template.render(body, unit) == "<p>Fine.</p>!"


def test_lines_are_joined_and_indents_dropped():
    assert template.render("<p>\n   {matrix}\n</p>", _unit(matrix="A1")) == "<p>A1</p>"


def test_if_blocks_nest():
    body = "{if extras}a{if matrix}b{end}c{end}"
    assert template.render(body, _unit(extras="x", matrix="y")) == "abc"
    assert template.render(body, _unit(extras="x")) == "ac"
    assert template.render(body, _unit(matrix="y")) == ""


@pytest.mark.parametrize(
    "body, why",
    [
        ("{nope}", "not something a template knows"),
        ("{if nope}x{end}", "not something a template knows"),
        ("{if extras}x", "never closed"),
        ("x{end}", "no {if}"),
        ("a } b", "stray"),
        ("   ", "empty"),
    ],
)
def test_a_bad_template_says_what_is_wrong(body, why):
    with pytest.raises(TemplateError) as e:
        template.validate(body)
    assert why in str(e.value)


def test_a_saved_template_is_used_for_new_drafts():
    fbc.db.session.add(DescriptionTemplate(name="ebay", body="Record: {record_note}"))
    fbc.db.session.commit()
    assert describe.build_description(_unit(record_note="Fine.")) == "Record: Fine."


@pytest.fixture
def client():
    sm = fbc.appbuilder.sm
    sm.create_db()
    fbc.appbuilder.add_permissions(update_perms=True)
    sm.add_user("admin", "A", "Dmin", "admin@example.com", sm.find_role("Admin"), "pw")
    client = fbc.app.test_client()
    client.post("/login/", data={"username": "admin", "password": "pw"})
    return client


def _page(client, **form):
    return client.post("/descriptiontemplate/", data=form, follow_redirects=True)


def test_the_page_shows_the_built_in_template_and_a_preview(client):
    fbc.db.session.add(_unit(record_note="Plays great."))
    fbc.db.session.commit()
    html = client.get("/descriptiontemplate/").get_data(as_text=True)
    assert "built-in one, not saved yet" in html
    assert "{if sleeve_grade or sleeve_note" in html
    assert "Plays great." in html


def test_saving_keeps_the_template_and_reset_throws_it_away(client):
    saved = _page(client, action="save", body="X {record_note}")
    assert "Saved." in saved.get_data(as_text=True)
    assert template.saved() == "X {record_note}"
    _page(client, action="reset")
    assert template.saved() is None


def test_a_bad_template_is_not_saved(client):
    html = _page(client, action="save", body="{nope}").get_data(as_text=True)
    assert "not something a template knows" in html
    assert template.saved() is None


def test_preview_does_not_save(client):
    unit = _unit(record_note="Quiet.")
    fbc.db.session.add(unit)
    fbc.db.session.commit()
    html = _page(
        client, action="preview", body="<b>{record_note}</b>", unit=str(unit.id)
    ).get_data(as_text=True)
    assert "&lt;b&gt;Quiet.&lt;/b&gt;" in html
    assert template.saved() is None
