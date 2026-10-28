import datetime
import json

from fbc import db
from fbc.models.discogs import Artist, DiscogsRelease, Genre, Style
from fbc.util import add_discogs_release


class FakeArtist:
    def __init__(self, id, name):
        self.id = id
        self.name = name


class FakeMaster:
    def __init__(self, id, year):
        self.id = id
        self.year = year


class FakeRelease:
    def __init__(
        self,
        id,
        title,
        year,
        artists=None,
        genres=None,
        styles=None,
        master=None,
        formats=None,
        released="2020-01-01",
    ):
        self.id = id
        self.title = title
        self.year = year
        self.artists = artists or []
        self.genres = genres or []
        self.styles = styles or []
        self.master = master
        self.formats = formats or [{"name": "Vinyl", "qty": "1"}]
        self._released = released

    def fetch(self, key):
        assert key == "released"
        return self._released


class FakeCollectionItem:
    def __init__(
        self, release, folder_id=1, instance_id=1000, notes=None, date_added=None
    ):
        self.id = release.id
        self.instance_id = instance_id
        self.release = release
        self.folder_id = folder_id
        self.notes = notes if notes is not None else []
        self.date_added = date_added or datetime.date(2024, 1, 1)


def test_add_discogs_release_creates_new_release_with_artists_genres_styles(app):
    release = FakeRelease(
        id=1,
        title="Discovery",
        year=2001,
        artists=[FakeArtist(id=10, name="Daft Punk")],
        genres=["Electronic"],
        styles=["House"],
        master=FakeMaster(id=99, year=2001),
    )
    item = FakeCollectionItem(release, folder_id=5, instance_id=555)

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


def test_add_discogs_release_strips_and_sorts_the_prefixed_artist_names(app):
    release = FakeRelease(
        id=2,
        title="Abbey Road",
        year=1969,
        artists=[FakeArtist(id=20, name="The Beatles")],
    )
    item = FakeCollectionItem(release)

    add_discogs_release(item)

    artist = db.session.query(Artist).filter_by(artist_id=20).first()
    assert artist.name == "The Beatles"
    assert artist.sort_name == "Beatles, The"


def test_add_discogs_release_reuses_existing_artist_genre_style_rows(app):
    db.session.add(Artist(artist_id=10, name="Daft Punk", sort_name=""))
    db.session.add(Genre(name="Electronic"))
    db.session.commit()

    release = FakeRelease(
        id=1,
        title="Discovery",
        year=2001,
        artists=[FakeArtist(id=10, name="Daft Punk")],
        genres=["Electronic"],
    )
    item = FakeCollectionItem(release)

    add_discogs_release(item)

    assert db.session.query(Artist).count() == 1
    assert db.session.query(Genre).count() == 1


def test_add_discogs_release_without_master_uses_release_year_and_zero_id(app):
    release = FakeRelease(id=3, title="No Master", year=2015, master=None)
    item = FakeCollectionItem(release)

    add_discogs_release(item)

    dr = db.session.query(DiscogsRelease).filter_by(release_id=3).first()
    assert dr.master_id == 0
    assert dr.master_year == 2015


def test_add_discogs_release_updates_existing_release(app):
    release = FakeRelease(id=4, title="Original Title", year=2000)
    item = FakeCollectionItem(release, instance_id=100)
    add_discogs_release(item)

    updated_release = FakeRelease(id=4, title="Updated Title", year=2001)
    updated_item = FakeCollectionItem(updated_release, instance_id=100)
    add_discogs_release(updated_item)

    assert db.session.query(DiscogsRelease).filter_by(release_id=4).count() == 1
    dr = db.session.query(DiscogsRelease).filter_by(release_id=4).first()
    assert dr.title == "Updated Title"
    assert dr.year == 2001


def test_add_discogs_release_appends_new_instance_to_existing_release(app):
    release = FakeRelease(id=5, title="Multi Copy", year=2010)
    item1 = FakeCollectionItem(release, instance_id=1)
    add_discogs_release(item1)

    item2 = FakeCollectionItem(release, instance_id=2)
    add_discogs_release(item2)

    dr = db.session.query(DiscogsRelease).filter_by(release_id=5).first()
    instance_ids = sorted(i.instance_id for i in dr.discogs_instances)
    assert instance_ids == [1, 2]


def test_add_discogs_release_does_not_duplicate_existing_instance(app):
    release = FakeRelease(id=6, title="Single Copy", year=2010)
    item = FakeCollectionItem(release, instance_id=1)
    add_discogs_release(item)
    add_discogs_release(item)

    dr = db.session.query(DiscogsRelease).filter_by(release_id=6).first()
    assert [i.instance_id for i in dr.discogs_instances] == [1]


def test_add_discogs_release_serializes_formats_and_notes_as_json(app):
    release = FakeRelease(
        id=7,
        title="With Notes",
        year=2022,
        formats=[{"name": "Vinyl", "qty": "2"}],
    )
    item = FakeCollectionItem(release, notes=[{"field_id": 1, "value": "Mint (M)"}])

    add_discogs_release(item)

    dr = db.session.query(DiscogsRelease).filter_by(release_id=7).first()
    assert json.loads(dr.formats) == [{"name": "Vinyl", "qty": "2"}]
    assert json.loads(dr.notes) == [{"field_id": 1, "value": "Mint (M)"}]
