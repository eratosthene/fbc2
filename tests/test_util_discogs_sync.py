import json

import pytest
from fake_discogs import FakeDiscogs, collection_item

from fbc import db
from fbc.models.discogs import Artist, DiscogsRelease, Genre, Style
from fbc.util import add_discogs_release


@pytest.fixture
def discogs(monkeypatch):
    """Discogs's own record of each release (country, released) and master."""
    fake = FakeDiscogs()

    class Records(dict):
        def __missing__(self, key):
            return {"id": key, "country": "US", "released": "2020-01-01"}

    fake.releases = Records()
    fake.masters = {99: {"id": 99, "year": 2001}}
    return fake.install(monkeypatch)


def test_add_discogs_release_creates_new_release_with_artists_genres_styles(
    app, discogs
):
    item = collection_item(
        1,
        555,
        folder_id=5,
        title="Discovery",
        year=2001,
        artists=[(10, "Daft Punk")],
        genres=["Electronic"],
        styles=["House"],
        master_id=99,
    )

    add_discogs_release(item)

    dr = db.session.query(DiscogsRelease).filter_by(release_id=1).first()
    assert dr is not None
    assert dr.title == "Discovery"
    assert dr.year == 2001
    assert dr.master_id == 99
    assert dr.master_year == 2001
    assert dr.folder_id == 5
    assert [a.name for a in dr.artists] == ["Daft Punk"]
    assert [g.name for g in dr.genres] == ["Electronic"]
    assert [s.name for s in dr.styles] == ["House"]
    assert [i.instance_id for i in dr.discogs_instances] == [555]

    # The underlying Artist/Genre/Style rows are deduplicated/created.
    assert db.session.query(Artist).count() == 1
    assert db.session.query(Genre).count() == 1
    assert db.session.query(Style).count() == 1


def test_the_master_year_wins_over_the_release_year(app, discogs):
    discogs.masters[99] = {"id": 99, "year": 1969}

    add_discogs_release(collection_item(1, 1, year=1990, master_id=99))

    dr = db.session.query(DiscogsRelease).filter_by(release_id=1).first()
    assert (dr.year, dr.master_year) == (1990, 1969)


def test_add_discogs_release_strips_and_sorts_the_prefixed_artist_names(app, discogs):
    add_discogs_release(
        collection_item(2, 1, title="Abbey Road", artists=[(20, "The Beatles")])
    )

    artist = db.session.query(Artist).filter_by(artist_id=20).first()
    assert artist.name == "The Beatles"
    assert artist.sort_name == "Beatles, The"


def test_add_discogs_release_reuses_existing_artist_genre_style_rows(app, discogs):
    db.session.add(Artist(artist_id=10, name="Daft Punk", sort_name=""))
    db.session.add(Genre(name="Electronic"))
    db.session.commit()

    add_discogs_release(
        collection_item(
            1, 1, artists=[(10, "Daft Punk")], genres=["Electronic"], styles=[]
        )
    )

    assert db.session.query(Artist).count() == 1
    assert db.session.query(Genre).count() == 1


def test_add_discogs_release_without_master_uses_release_year_and_zero_id(app, discogs):
    add_discogs_release(collection_item(3, 1, year=2015, master_id=0))

    dr = db.session.query(DiscogsRelease).filter_by(release_id=3).first()
    assert dr.master_id == 0
    assert dr.master_year == 2015
    assert "master" not in discogs.names()  # nothing to look up


def test_add_discogs_release_updates_existing_release(app, discogs):
    add_discogs_release(collection_item(4, 100, title="Original Title", year=2000))

    add_discogs_release(collection_item(4, 100, title="Updated Title", year=2001))

    assert db.session.query(DiscogsRelease).filter_by(release_id=4).count() == 1
    dr = db.session.query(DiscogsRelease).filter_by(release_id=4).first()
    assert dr.title == "Updated Title"
    assert dr.year == 2001


def test_add_discogs_release_appends_new_instance_to_existing_release(app, discogs):
    add_discogs_release(collection_item(5, 1, title="Multi Copy"))

    add_discogs_release(collection_item(5, 2, title="Multi Copy"))

    dr = db.session.query(DiscogsRelease).filter_by(release_id=5).first()
    instance_ids = sorted(i.instance_id for i in dr.discogs_instances)
    assert instance_ids == [1, 2]


def test_add_discogs_release_does_not_duplicate_existing_instance(app, discogs):
    item = collection_item(6, 1, title="Single Copy")
    add_discogs_release(item)
    add_discogs_release(item)

    dr = db.session.query(DiscogsRelease).filter_by(release_id=6).first()
    assert [i.instance_id for i in dr.discogs_instances] == [1]


def test_add_discogs_release_serializes_formats_and_notes_as_json(app, discogs):
    item = collection_item(
        7,
        1,
        title="With Notes",
        formats=[{"name": "Vinyl", "qty": "2"}],
        notes=[{"field_id": 1, "value": "Mint (M)"}],
    )

    add_discogs_release(item)

    dr = db.session.query(DiscogsRelease).filter_by(release_id=7).first()
    assert json.loads(dr.formats) == [{"name": "Vinyl", "qty": "2"}]
    assert json.loads(dr.notes) == [{"field_id": 1, "value": "Mint (M)"}]


def test_add_discogs_release_stores_labels_country_and_release_date(app, discogs):
    discogs.releases[8] = {"id": 8, "country": "UK", "released": "1980-05-02"}

    add_discogs_release(
        collection_item(
            8,
            1,
            labels=[{"name": "MCA Records", "catno": "MCA-5149"}],
            title="Carnaval",
        )
    )

    dr = db.session.query(DiscogsRelease).filter_by(release_id=8).first()
    assert json.loads(dr.labels) == [{"name": "MCA Records", "catno": "MCA-5149"}]
    assert dr.country == "UK"
    assert dr.released == "1980-05-02"


def test_the_date_added_is_the_date_discogs_gave(app, discogs):
    add_discogs_release(collection_item(9, 1, date_added="2024-03-05T10:00:00-08:00"))

    dr = db.session.query(DiscogsRelease).filter_by(release_id=9).first()
    assert str(dr.date_added)[:10] == "2024-03-05"
