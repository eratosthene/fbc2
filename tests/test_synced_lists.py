"""The line above a synced list that says when its data last synced."""

import datetime

import pytest

import fbc
from fbc import db, sync, syncjobs
from fbc.models.sync import SyncRecord

# 5:45 PM in Central, in winter.
FINISHED = datetime.datetime(2026, 11, 13, 23, 45)

# view path -> the syncs it shows, labelled.
VIEWS = {
    "discogsreleasemodelview": ["Discogs collection"],
    "discogsreleasemodelnounitview": ["Discogs collection"],
    "artistmodelview": ["Discogs collection"],
    "genremodelview": ["Discogs collection"],
    "stylemodelview": ["Discogs collection"],
    "foldermodelview": ["Discogs collection"],
    "releasesneedingunitsview": ["Discogs collection"],
    "discogslistingmodelview": ["Discogs listings"],
    "ebaylistingmodelview": ["eBay listings"],
    "unitsneedingdraftsview": ["eBay listings"],
    "ebayordermodelview": ["eBay orders", "eBay listings"],
    "orderstoshipview": ["eBay orders", "eBay listings"],
}

NAMES = {
    "Discogs collection": syncjobs.DISCOGS_SYNC,
    "Discogs listings": syncjobs.DISCOGS_LISTINGS_SYNC,
    "eBay listings": sync.LISTINGS_SYNC,
    "eBay orders": syncjobs.ORDERS_SYNC,
}


@pytest.fixture
def client():
    sm = fbc.appbuilder.sm
    sm.create_db()
    fbc.appbuilder.add_permissions(update_perms=True)
    sm.add_user("admin", "A", "Dmin", "admin@example.com", sm.find_role("Admin"), "pw")
    client = fbc.app.test_client()
    client.post("/login/", data={"username": "admin", "password": "pw"})
    return client


def _page(client, view):
    response = client.get(f"/{view}/list/")
    assert response.status_code == 200
    return response.get_data(as_text=True)


@pytest.mark.parametrize("view", VIEWS)
def test_a_list_shows_when_each_of_its_syncs_last_finished(client, view):
    db.session.add_all(
        SyncRecord(name=name, finished=FINISHED) for name in NAMES.values()
    )
    db.session.commit()

    page = _page(client, view)

    for label in VIEWS[view]:
        assert f"{label}</a> last synced 2026-11-13 5:45 PM." in page
    assert "Never synced" not in page


@pytest.mark.parametrize("view", VIEWS)
def test_a_list_says_never_synced_without_a_record(client, view):
    page = _page(client, view)

    for label in VIEWS[view]:
        assert f"{label}</a>: Never synced." in page


def test_a_list_only_shows_its_own_syncs(client):
    db.session.add(SyncRecord(name=syncjobs.ORDERS_SYNC, finished=FINISHED))
    db.session.commit()

    page = _page(client, "ebaylistingmodelview")

    assert "eBay orders" not in page
    assert "eBay listings</a>: Never synced." in page
