"""The Columns button's markup: every heading and cell carries its column's name."""

import datetime
import re

import pytest

import fbc
from fbc import db
from fbc.models.inventory import PurchaseLot, Unit


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


def _cells(html, tag):
    """The (tag, data-col or None) of every cell in the table."""
    table = html[html.index('<table class="table table-bordered') :]
    return re.findall(rf"<{tag}\b([^>]*)>", table.split("</table>")[0])


def _names(attrs):
    return [
        m.group(1) if (m := re.search(r'data-col="([^"]+)"', a)) else None
        for a in attrs
    ]


def test_unit_list_cells_carry_their_column_name(client):
    lot = PurchaseLot(name="Lot", date=datetime.date(2024, 1, 1), price=10.0)
    db.session.add_all(
        [
            lot,
            Unit(
                name="U", unit_type='12" LP', pressing="", grading="", purchase_lot=lot
            ),
        ]
    )
    db.session.commit()

    html = _page(client, "unitmodelview")

    heads = _names(_cells(html, "th"))
    cells = _names(_cells(html, "td"))
    movable = [n for n in heads if n]
    assert "name" in movable
    # The checkbox and button columns have no name; every other one does.
    assert heads[: len(heads) - len(movable)] == [None] * (len(heads) - len(movable))
    assert cells[: len(heads) - len(movable)] == [None] * (len(heads) - len(movable))
    assert cells[len(heads) - len(movable) :] == movable
    assert 'class="fbc-columns"' in html and "js/list-columns.js" in html


def test_discogs_listings_widget_cells_carry_their_column_name(client):
    from fbc.models.discogs import DiscogsListing

    unit = Unit(
        name="U",
        unit_type='12" LP',
        pressing="",
        grading="",
        purchase_lot=PurchaseLot(name="L", date=datetime.date(2024, 1, 1), price=1.0),
    )
    db.session.add(unit)
    db.session.add(DiscogsListing(listing_id=1, status="For Sale", unit=unit))
    db.session.commit()

    html = _page(client, "discogslistingmodelview")

    heads = [n for n in _names(_cells(html, "th")) if n]
    cells = [n for n in _names(_cells(html, "td")) if n]
    assert heads and cells == heads


def test_an_empty_list_has_no_table(client):
    assert '<table class="table table-bordered' not in _page(client, "unitmodelview")
