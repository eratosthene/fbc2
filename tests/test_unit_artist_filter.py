"""The Discogs Artist filter on the Units lists."""

import pytest
from auth import signed_in_client

import fbc
from fbc.models.discogs import Artist, DiscogsRelease
from fbc.models.inventory import Unit


@pytest.fixture
def client(app):
    return signed_in_client(app)


def _release(rid, *artists):
    release = DiscogsRelease(release_id=rid, title="T%d" % rid, artists=list(artists))
    fbc.db.session.add(release)
    return release


def _unit(name, release=None):
    fbc.db.session.add(Unit(name=name, retail_price=10.0, discogs_release=release))


def _names(client, url):
    html = client.get(url).get_data(as_text=True)
    return {
        n for n in ("Abba LP", "Duo LP", "Other LP", "Loose") if ">" + n + "<" in html
    }


@pytest.fixture
def stock(app):
    abba = Artist(artist_id=1, name="ABBA", sort_name="ABBA")
    bee = Artist(artist_id=2, name="Bee", sort_name="Bee")
    fbc.db.session.add_all([abba, bee])
    _unit("Abba LP", _release(10, abba))
    _unit("Duo LP", _release(11, abba, bee))
    _unit("Other LP", _release(12, bee))
    _unit("Loose")
    fbc.db.session.commit()


def test_offered_in_search_with_artists(client, stock):
    html = client.get("/unitmodelview/list/").get_data(as_text=True)
    assert "Discogs Artist" in html
    assert 'value="1"' in html


def test_only_that_artists_units(client, stock):
    got = _names(client, "/unitmodelview/list/?_flt_0_discogs_artist=2")
    assert got == {"Duo LP", "Other LP"}


def test_release_with_several_artists_matches_each(client, stock):
    assert "Duo LP" in _names(client, "/unitmodelview/list/?_flt_0_discogs_artist=1")
    assert "Duo LP" in _names(client, "/unitmodelview/list/?_flt_0_discogs_artist=2")


def test_unit_without_release_is_not_matched(client, stock):
    for artist in ("1", "2"):
        url = "/unitmodelview/list/?_flt_0_discogs_artist=" + artist
        assert "Loose" not in _names(client, url)
    assert "Loose" in _names(client, "/unitmodelview/list/")


def test_task_list_has_it_too(client, stock):
    url = "/unitsneedingdraftsview/list/?_flt_0_discogs_artist=2"
    assert _names(client, url) <= {"Duo LP", "Other LP"}
