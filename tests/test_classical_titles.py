"""Reissue years, "/" between artists, and classical records' credits."""

import datetime
import importlib.util
import itertools
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fake_discogs import FakeDiscogs, collection_item

from fbc import db
from fbc.discogs.saving import add_discogs_release, save_record
from fbc.ebay import credits, describe, drafts
from fbc.ebay.describe import build_item_specifics, build_title
from fbc.models.discogs import Artist, DiscogsRelease, Genre, Style
from fbc.models.inventory import PurchaseLot, Unit


_ids = itertools.count(1000)


def _get_or_make(model, name):
    return db.session.query(model).filter_by(name=name).first() or model(name=name)


def _unit(
    title="Album",
    artists=("Band",),
    year=1975,
    master_year=1960,
    genres=("Rock",),
    styles=(),
    credit_list=None,
    pressing="US Reissue",
    release_id=1,
    **unit_kwargs,
):
    release = DiscogsRelease(
        release_id=release_id,
        title=title,
        year=year,
        master_year=master_year,
        formats=json.dumps([{"name": "Vinyl", "descriptions": ["LP"]}]),
        labels=json.dumps([{"name": "Label", "catno": "L-1"}]),
        credits=None if credit_list is None else json.dumps(credit_list),
        country="US",
        notes="[]",
    )
    release.artists = [Artist(artist_id=next(_ids), name=n) for n in artists]
    release.genres = [_get_or_make(Genre, g) for g in genres]
    release.styles = [_get_or_make(Style, s) for s in styles]
    lot = PurchaseLot(name="Lot", date=datetime.date(2024, 1, 1), price=10.0)
    fields = dict(
        name="Unit",
        unit_type='12" LP',
        grading="VG/VG",
        pressing=pressing,
        retail_price=5.0,
        sold=False,
    )
    fields.update(unit_kwargs)
    unit = Unit(discogs_release=release, purchase_lot=lot, **fields)
    db.session.add(unit)
    db.session.commit()
    return unit


# Reissue titles


def test_several_artists_are_joined_with_a_slash_and_no_spaces(app):
    unit = _unit(artists=("Sonny", "Cher (2)"), pressing="")
    names = [a.name for a in unit.discogs_release.artists]
    assert build_title(unit) == "/".join(names).replace("Cher (2)", "Cher") + (
        " - Album 1975 Vinyl LP VG"
    )
    assert "/" in build_title(unit).split(" - ")[0]
    assert sorted(build_item_specifics(unit)["Artist"]) == ["Cher", "Sonny"]


def test_a_reissue_shows_the_master_year_then_its_own_year(app):
    assert build_title(_unit()) == "Band - Album 1960 Vinyl LP Reissue 1975 VG"


def test_an_original_keeps_the_release_year(app):
    unit = _unit(pressing="US Original")
    assert build_title(unit) == "Band - Album 1975 First Edition Vinyl LP VG"


def test_a_reissue_from_the_masters_own_year_has_no_second_year(app):
    unit = _unit(year=1975, master_year=1975)
    assert build_title(unit) == "Band - Album 1975 Vinyl LP Reissue VG"


@pytest.mark.parametrize("master_year", [0, None])
def test_a_reissue_without_a_master_year_keeps_the_release_year(app, master_year):
    unit = _unit(master_year=master_year)
    assert build_title(unit) == "Band - Album 1975 Vinyl LP Reissue VG"


def test_a_reissue_without_a_year_of_its_own_uses_the_master_year(app):
    unit = _unit(year=None)
    assert build_title(unit) == "Band - Album 1960 Vinyl LP Reissue VG"


def test_a_non_reissue_pressing_never_uses_the_master_year(app):
    assert build_title(_unit(pressing="")) == "Band - Album 1975 Vinyl LP VG"


def _title_with(unit, length):
    """Set the album's title so the title without styles is `length` long."""
    unit.discogs_release.title = "T" * length
    return build_title(unit)


def test_the_title_is_cut_styles_first(app):
    unit = _unit(styles=("Hard Rock", "Blues Rock"))
    full = "Band - Album 1960 Vinyl LP Reissue 1975 Hard-Rock Blues-Rock VG"
    assert build_title(unit) == full
    # Room for the second year but not for all the styles.
    bare = "Band - Album 1960 Vinyl LP Reissue 1975 VG"
    unit.discogs_release.title = "T" * (80 - len(bare) + len("Album"))
    title = build_title(unit)
    assert len(title) == 80
    assert title.endswith("Vinyl LP Reissue 1975 VG")


def test_then_the_year_after_reissue_goes(app):
    unit = _unit(styles=("Prog",))
    # One character too long with the second year, and no styles.
    without_year = "Band -  1960 Vinyl LP Reissue VG"
    title = _title_with(unit, 80 - len(without_year))
    assert title == "Band - " + "T" * 48 + " 1960 Vinyl LP Reissue VG"
    assert len(title) == 80
    assert "1975" not in title and "Prog" not in title


def test_then_the_edition_goes(app):
    unit = _unit()
    without_edition = "Band -  1960 Vinyl LP VG"
    title = _title_with(unit, 80 - len(without_edition))
    assert title == "Band - " + "T" * 56 + " 1960 Vinyl LP VG"
    assert "Reissue" not in title


def test_what_is_left_is_cut_to_80(app):
    unit = _unit()
    unit.discogs_release.title = "T" * 100
    assert len(build_title(unit)) == 80


def test_release_year_falls_back_to_the_master_year(app):
    unit = _unit(year=None, master_year=1960)
    assert build_item_specifics(unit)["Release Year"] == ["1960"]


def test_release_year_prefers_the_release_year(app):
    assert build_item_specifics(_unit())["Release Year"] == ["1975"]


@pytest.mark.parametrize("master_year", [0, None, -1])
def test_release_year_is_left_out_with_no_usable_year(app, master_year):
    unit = _unit(year=0, master_year=master_year)
    assert "Release Year" not in build_item_specifics(unit)


# Credits from Discogs


def _record(artists=(), extra=(), tracks=()):
    return {
        "id": 1,
        "artists": [{"name": n, "role": r} for n, r in artists],
        "extraartists": [{"name": n, "role": r} for n, r in extra],
        "tracklist": [
            {"title": "T", "extraartists": [{"name": n, "role": r} for n, r in t]}
            for t in tracks
        ],
    }


def _people(record):
    release = DiscogsRelease(credits=credits.credits_json(record))
    return credits.people(release, describe.clean_name)


def test_role_credits_give_composer_conductor_and_orchestra():
    record = _record(
        artists=[("Berliner Philharmoniker", "")],
        extra=[
            ("Richard Strauss", "Composed By"),
            ("Herbert von Karajan", "Conductor"),
            ("Berliner Philharmoniker", "Orchestra"),
            ("Someone Else", "Engineer"),
        ],
    )
    assert _people(record) == {
        "composer": ["Richard Strauss"],
        "conductor": ["Herbert von Karajan"],
        "orchestra": ["Berliner Philharmoniker"],
    }


def test_written_by_and_music_by_are_composers_and_ensemble_an_orchestra():
    record = _record(
        extra=[
            ("A", "Written-By"),
            ("B", "Music By"),
            ("Ensemble X", "Ensemble"),
            ("L", "Lyrics By"),
        ]
    )
    people = _people(record)
    assert people["composer"] == ["A", "B"]
    assert people["orchestra"] == ["Ensemble X"]


def test_roles_may_be_combined_and_annotated():
    record = _record(
        extra=[("Georg Solti (2)", "Conductor, Chorus Master [Uncredited]")]
    )
    assert _people(record)["conductor"] == ["Georg Solti"]


def test_a_composer_credited_on_a_track_counts():
    record = _record(
        artists=[("Some Band", "")],
        tracks=[[("Gustav Holst", "Composed By")], [("Gustav Holst", "Written-By")]],
    )
    assert _people(record)["composer"] == ["Gustav Holst"]


def test_main_artists_without_roles_are_not_guessed_as_composer_or_conductor():
    record = _record(artists=[("Ludwig van Beethoven", ""), ("Leonard Bernstein", "")])
    assert _people(record) == {"composer": [], "conductor": [], "orchestra": []}


def test_a_main_artist_named_like_an_orchestra_is_the_orchestra():
    record = _record(
        artists=[
            ("Ludwig van Beethoven", ""),
            ("Wiener Philharmoniker", ""),
            ("Karl Böhm", ""),
        ]
    )
    assert _people(record) == {
        "composer": [],
        "conductor": [],
        "orchestra": ["Wiener Philharmoniker"],
    }


def test_a_role_credited_orchestra_beats_a_main_artist_one():
    record = _record(
        artists=[("London Symphony Orchestra", "")],
        extra=[("Orchestra Of The Royal Opera House", "Orchestra")],
    )
    assert _people(record)["orchestra"] == ["Orchestra Of The Royal Opera House"]


def test_various_is_not_a_person():
    record = _record(extra=[("Various", "Composed By")])
    assert _people(record)["composer"] == []


@pytest.mark.parametrize("record", [{}, {"artists": None, "extraartists": None}])
def test_a_release_without_credits_gives_nothing(record):
    assert credits.extract(record) == []
    assert _people(record) == {"composer": [], "conductor": [], "orchestra": []}


@pytest.mark.parametrize("stored", [None, "", "not json", '{"a": 1}'])
def test_unreadable_stored_credits_give_nothing(stored):
    assert credits.load(DiscogsRelease(credits=stored)) == []


def test_only_the_credits_in_use_are_kept():
    record = _record(
        artists=[("Main", "")],
        extra=[("Eng", "Engineer"), ("Con", "Conductor"), ("Con", "Conductor")],
    )
    assert credits.extract(record) == [
        {"name": "Main", "role": ""},
        {"name": "Con", "role": "Conductor"},
    ]


# Classical detection


def _credits(*pairs):
    return [{"name": n, "role": r} for n, r in pairs]


STRAUSS = _credits(
    ("Richard Strauss", "Composed By"),
    ("Herbert von Karajan", "Conductor"),
    ("Berliner Philharmoniker", "Orchestra"),
)


def test_classical_genre_and_opera_style_are_classical(app):
    assert credits.is_classical(_unit(genres=("Classical",)).discogs_release)
    opera = _unit(genres=("Stage & Screen",), styles=("Opera",), release_id=2)
    assert credits.is_classical(opera.discogs_release)
    assert not credits.is_classical(
        _unit(genres=("Jazz",), release_id=3).discogs_release
    )
    assert not credits.is_classical(None)


def test_non_classical_records_ignore_credits(app):
    unit = _unit(genres=("Rock",), credit_list=STRAUSS, pressing="")
    specifics = build_item_specifics(unit)
    assert "Composer" not in specifics
    assert build_title(unit).startswith("Band - Album")


# Classical specifics and title


def test_classical_specifics_have_composer_conductor_and_orchestra(app):
    unit = _unit(genres=("Classical",), credit_list=STRAUSS)
    specifics = build_item_specifics(unit)
    assert specifics["Composer"] == ["Richard Strauss"]
    assert specifics["Conductor"] == ["Herbert von Karajan"]
    assert specifics["Performer Orchestra"] == ["Berliner Philharmoniker"]


def test_classical_specifics_leave_out_what_isnt_known(app):
    unit = _unit(genres=("Classical",), credit_list=[])
    specifics = build_item_specifics(unit)
    for name in ("Composer", "Conductor", "Performer Orchestra"):
        assert name not in specifics


def test_classical_title_uses_composer_and_conductor_surnames(app):
    unit = _unit(
        title="Also Sprach Zarathustra",
        genres=("Classical",),
        credit_list=STRAUSS,
        pressing="",
        year=1975,
        master_year=1975,
    )
    assert (
        build_title(unit)
        == "Strauss/Karajan - Also Sprach Zarathustra 1975 Vinyl LP VG"
    )


def test_classical_title_keeps_reissue_years_and_cutting(app):
    unit = _unit(
        title="Also Sprach Zarathustra",
        genres=("Classical",),
        styles=("Romantic",),
        credit_list=STRAUSS,
    )
    assert build_title(unit) == (
        "Strauss/Karajan - Also Sprach Zarathustra 1960 Vinyl LP Reissue 1975"
        " Romantic VG"
    )
    unit.discogs_release.title = "Also Sprach Zarathustra, Op. 30 (Complete Edition)"
    title = build_title(unit)
    assert len(title) <= 80
    assert title.startswith("Strauss/Karajan - Also Sprach")


def test_classical_title_with_only_one_name_known(app):
    only_conductor = _unit(
        genres=("Classical",),
        credit_list=_credits(("Herbert von Karajan", "Conductor")),
        pressing="",
    )
    assert build_title(only_conductor).startswith("Karajan - Album")
    only_composer = _unit(
        genres=("Classical",),
        credit_list=_credits(("Richard Strauss", "Composed By")),
        pressing="",
        release_id=2,
    )
    assert build_title(only_composer).startswith("Strauss - Album")


def test_classical_title_with_neither_uses_the_artists(app):
    unit = _unit(genres=("Classical",), artists=("A", "B"), credit_list=[], pressing="")
    assert sorted(build_title(unit).split(" - ")[0].split("/")) == ["A", "B"]
    never_fetched = _unit(genres=("Classical",), credit_list=None, release_id=2)
    assert build_title(never_fetched).startswith("Band - Album")


def test_classical_title_skips_a_part_with_several_names(app):
    unit = _unit(
        genres=("Classical",),
        credit_list=_credits(
            ("Mozart", "Composed By"),
            ("Haydn", "Composed By"),
            ("Karajan", "Conductor"),
        ),
        pressing="",
    )
    assert build_title(unit).startswith("Karajan - Album")


def test_composer_who_also_conducts_is_named_once(app):
    unit = _unit(
        genres=("Classical",),
        credit_list=_credits(
            ("Leonard Bernstein", "Composed By"), ("Leonard Bernstein", "Conductor")
        ),
        pressing="",
    )
    assert build_title(unit).startswith("Bernstein - Album")


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Herbert von Karajan", "Karajan"),
        ("Ludwig van Beethoven", "Beethoven"),
        ("Carl Maria von Weber", "Weber"),
        ("Richard Strauss", "Strauss"),
        ("Johann Strauss II", "Strauss"),
        ("Ralph Vaughan Williams", "Vaughan Williams"),
        ("Vaughan Williams", "Vaughan Williams"),
        ("Andrew Lloyd Webber", "Lloyd Webber"),
        ("Nikolai Rimsky-Korsakov", "Rimsky-Korsakov"),
        ("Camille Saint-Saëns", "Saint-Saëns"),
        ("Sir Georg Solti", "Solti"),
        ("Manuel de Falla", "Falla"),
        ("Martin Luther King Jr.", "King"),
        ("Bach", "Bach"),
        ("", ""),
    ],
)
def test_surname(name, expected):
    assert credits.surname(name) == expected


# Storage


def test_a_new_release_from_a_sync_stores_the_credits_without_another_call(
    app, monkeypatch
):
    fake = FakeDiscogs()
    fake.releases = {
        10: {
            "id": 10,
            "country": "DE",
            "released": "1975",
            "artists": [{"id": 1, "name": "Richard Strauss"}],
            "extraartists": [{"name": "Herbert von Karajan", "role": "Conductor"}],
        }
    }
    fake.install(monkeypatch)

    add_discogs_release(collection_item(10, 1, genres=("Classical",)))

    release = db.session.query(DiscogsRelease).filter_by(release_id=10).one()
    assert json.loads(release.credits) == [
        {"name": "Richard Strauss", "role": ""},
        {"name": "Herbert von Karajan", "role": "Conductor"},
    ]
    assert len(fake.calls_to("release")) == 1


def test_a_sync_fills_in_credits_on_a_release_that_has_none(app, monkeypatch):
    fake = FakeDiscogs()
    fake.releases = {
        11: {"id": 11, "extraartists": [{"name": "Con", "role": "Conductor"}]}
    }
    fake.install(monkeypatch)
    add_discogs_release(collection_item(11, 1))
    release = db.session.query(DiscogsRelease).filter_by(release_id=11).one()
    release.credits = None
    db.session.commit()

    add_discogs_release(collection_item(11, 2))

    db.session.refresh(release)
    assert json.loads(release.credits) == [{"name": "Con", "role": "Conductor"}]


def test_a_release_found_by_identify_stores_its_credits(app):
    release = save_record(
        {
            "id": 12,
            "title": "T",
            "artists": [{"id": 3, "name": "Composer"}],
            "extraartists": [{"name": "Orch", "role": "Orchestra"}],
        }
    )
    assert json.loads(release.credits) == [
        {"name": "Composer", "role": ""},
        {"name": "Orch", "role": "Orchestra"},
    ]


def _classical_without_credits(**kwargs):
    unit = _unit(genres=("Classical",), **kwargs)
    return unit.discogs_release


def test_a_classical_release_with_labels_but_no_credits_is_fetched_once(
    app, monkeypatch
):
    fake = FakeDiscogs()
    fake.releases = {
        1: {
            "id": 1,
            "country": "XX",
            "labels": [{"name": "Other", "catno": "O-1"}],
            "extraartists": [{"name": "Con", "role": "Conductor"}],
        }
    }
    fake.install(monkeypatch)
    release = _classical_without_credits()

    drafts.ensure_release_details(release)
    drafts.ensure_release_details(release)

    assert len(fake.calls_to("release")) == 1
    assert json.loads(release.credits) == [{"name": "Con", "role": "Conductor"}]
    # What it already had stays.
    assert json.loads(release.labels) == [{"name": "Label", "catno": "L-1"}]
    assert release.country == "US"


def test_a_release_with_no_credits_on_discogs_is_not_fetched_again(app, monkeypatch):
    fake = FakeDiscogs()
    fake.releases = {1: {"id": 1}}
    fake.install(monkeypatch)
    release = _classical_without_credits()

    drafts.ensure_release_details(release)
    drafts.ensure_release_details(release)

    assert release.credits == "[]"
    assert len(fake.calls_to("release")) == 1


def test_a_non_classical_release_with_labels_is_not_fetched(app, monkeypatch):
    fake = FakeDiscogs().install(monkeypatch)
    drafts.ensure_release_details(_unit().discogs_release)
    assert fake.calls_to("release") == []


def test_a_release_without_labels_gets_labels_country_and_credits(app, monkeypatch):
    fake = FakeDiscogs()
    fake.releases = {
        1: {
            "id": 1,
            "country": "UK",
            "labels": [{"name": "Decca", "catno": "D-1"}],
            "extraartists": [{"name": "Con", "role": "Conductor"}],
        }
    }
    fake.install(monkeypatch)
    release = _unit().discogs_release
    release.labels = None
    release.country = None

    drafts.ensure_release_details(release)

    assert json.loads(release.labels) == [{"name": "Decca", "catno": "D-1"}]
    assert release.country == "UK"
    assert json.loads(release.credits) == [{"name": "Con", "role": "Conductor"}]


def test_a_failed_fetch_leaves_the_release_alone(app, monkeypatch):
    def refuse(release_id):
        raise RuntimeError("dev mode")

    monkeypatch.setattr("fbc.discogs.client.release", refuse)
    release = _classical_without_credits()
    drafts.ensure_release_details(release)
    assert release.credits is None


def test_a_new_draft_for_a_classical_unit_uses_the_credits(app, monkeypatch):
    FakeDiscogs().install(monkeypatch)
    unit = _unit(
        title="Also Sprach Zarathustra",
        genres=("Classical",),
        credit_list=STRAUSS,
        pressing="",
        year=1975,
        master_year=1975,
    )
    draft = drafts.new_draft(unit)
    assert draft.title.startswith("Strauss/Karajan - Also Sprach Zarathustra")
    assert "Conductor: Herbert von Karajan" in draft.item_specifics
    assert "Karajan" not in (draft.description or "")


# The migration

VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"


def test_the_credits_column_is_added_empty_and_can_be_dropped():
    name = "c3e8a1d7f5b2_add_credits_to_discogs_release"
    spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    release = sa.Table(
        "discogs_release",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("labels", sa.Text),
    )
    with engine.begin() as conn:
        release.create(conn)
        conn.execute(release.insert().values(id=1, labels="[]"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert conn.execute(
            sa.text("SELECT id, labels, credits FROM discogs_release")
        ).one() == (1, "[]", None)
        conn.execute(sa.text("UPDATE discogs_release SET credits = '[]'"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        columns = [c["name"] for c in sa.inspect(conn).get_columns("discogs_release")]
        assert columns == ["id", "labels"]
