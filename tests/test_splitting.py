"""`flask split-descriptions`: old descriptions into the notes, never guessing."""

import pytest

from fbc.ebay import splitting
from fbc.models.inventory import Unit

HABIT = (
    "Cover has light ring wear. Original paper dust jacket with Capitol Records ads. "
    "Disc plays great, very little surface noise. "
    "Includes the poster. Ultrasonically cleaned."
)


def test_split_follows_the_owners_habit_and_drops_the_cleaning_line():
    notes, problem = splitting.split(HABIT)
    assert problem is None
    assert notes == {
        "sleeve_note": "Cover has light ring wear.",
        "dust_jacket_note": "Original paper dust jacket with Capitol Records ads.",
        "record_note": "Disc plays great, very little surface noise.",
        "extras": "Includes the poster.",
    }


def test_a_sentence_that_names_no_part_goes_on_with_the_one_before():
    notes, problem = splitting.split(
        "Cover is clean. Back shows some ringwear. Inside gatefold is pristine. "
        "Disc is quiet. Looks like new."
    )
    assert problem is None
    assert notes == {
        "sleeve_note": "Cover is clean. Back shows some ringwear. Inside gatefold is pristine.",
        "record_note": "Disc is quiet. Looks like new.",
    }


def test_words_inside_a_sentence_dont_move_it():
    # "Records" and "split" are about the jacket and the cover, not the disc.
    notes, problem = splitting.split(
        "Cover looks good, top is split. Clear dust jacket from 20th Century Records."
    )
    assert problem is None
    assert set(notes) == {"sleeve_note", "dust_jacket_note"}


def test_html_entities_are_turned_back_into_text():
    notes, _ = splitting.split("Cover has a 4&quot; tear.")
    assert notes == {"sleeve_note": 'Cover has a 4" tear.'}


@pytest.mark.parametrize(
    "text, why",
    [
        ("Nice copy.", "doesn't say which part"),
        ("Coever is fine. Disc plays.", "doesn't say which part"),
        ("Ultrasonically cleaned.", "no sentences left"),
        ("", "no sentences left"),
    ],
)
def test_split_refuses_what_it_would_have_to_guess(text, why):
    notes, problem = splitting.split(text)
    assert notes is None
    assert why in problem


def _unit(db, description, **fields):
    unit = Unit(name="U", description=description, **fields)
    db.session.add(unit)
    db.session.commit()
    return unit


def test_dry_run_changes_nothing(db):
    unit = _unit(db, HABIT)
    lines = []
    counts = splitting.split_all(echo=lines.append)
    assert counts["split"] == 1
    db.session.refresh(unit)
    assert unit.record_note is None


def test_apply_fills_the_notes_and_keeps_the_description(db):
    unit = _unit(db, HABIT)
    splitting.split_all(apply=True, echo=lambda _: None)
    db.session.refresh(unit)
    assert unit.sleeve_note == "Cover has light ring wear."
    assert unit.extras == "Includes the poster."
    assert unit.description == HABIT


def test_units_that_dont_split_are_listed_with_their_text(db):
    bad = _unit(db, "Nice copy.")
    lines = []
    counts = splitting.split_all(apply=True, echo=lines.append)
    assert counts["problems"] == 1
    assert any("#" + str(bad.id) in line for line in lines)
    assert any("Nice copy." in line for line in lines)
    db.session.refresh(bad)
    assert bad.record_note is None


def test_units_with_notes_or_no_description_are_left_alone(db):
    kept = _unit(db, HABIT, record_note="Mine.")
    _unit(db, "")
    _unit(db, None)
    counts = splitting.split_all(apply=True, echo=lambda _: None)
    assert counts == {"split": 0, "problems": 0, "skipped": 1}
    db.session.refresh(kept)
    assert kept.sleeve_note is None


def test_command_says_it_was_a_dry_run(app, db):
    _unit(db, HABIT)
    result = app.test_cli_runner().invoke(args=["split-descriptions"])
    assert "Would split 1 descriptions" in result.output
    assert "dry run" in result.output
    result = app.test_cli_runner().invoke(args=["split-descriptions", "--apply"])
    assert "Split 1 descriptions" in result.output
