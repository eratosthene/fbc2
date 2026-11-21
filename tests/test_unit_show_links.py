"""Unit Show has the same Links as the Units list (Unit.link_column)."""

import pytest
from flask import url_for
from auth import signed_in_client

import fbc
from fbc.core import localtime
from fbc.models.discogs import DiscogsListing, DiscogsRelease
from fbc.models.discogs import DiscogsOrder
from fbc.models.ebay import EbayListing, EbayOrder
from fbc.models.inventory import Unit
from fbc.models.sales import SalesReceipt


@pytest.fixture
def client(app):
    return signed_in_client(app)


def _add(*things):
    fbc.db.session.add_all(things)
    fbc.db.session.commit()


def _full_unit():
    release = DiscogsRelease(release_id=11, title="Carnaval", master_id=5)
    listing = EbayListing(item_id="123", title="Carnaval", price=12.5, url="http://e/1")
    receipt = SalesReceipt(
        date=localtime.utcnow().date(), sold_price=11.0, net_sold=9.0
    )
    receipt.ebay_orders.append(
        EbayOrder(
            order_id="E-1", created_time=localtime.utcnow(), buyer="b", price=11.0
        )
    )
    receipt.discogs_orders.append(
        DiscogsOrder(order_id="D-1", created_time=localtime.utcnow(), buyer="b")
    )
    unit = Unit(
        name="Full",
        discogs_release=release,
        discogs_master="5",
        ebay_listing=listing,
        sales_receipt=receipt,
        sold=True,
    )
    _add(unit)
    return unit


def _html(client, unit):
    page = client.get("/unitmodelview/show/%d" % unit.id)
    assert page.status_code == 200
    return page.get_data(as_text=True)


def test_show_has_every_link_the_list_has(client):
    unit = _full_unit()
    listing = DiscogsListing(listing_id=99, unit_id=unit.id, status="Sold", ended=True)
    _add(listing)

    html = _html(client, unit)

    assert "Links" in html
    for link in (
        "https://www.discogs.com/release/11",
        "https://www.discogs.com/master/5",
        "http://e/1",
        "/unitpictures/show/%d" % unit.id,
        "/salesreceiptmodelview/show/%d" % unit.sales_receipt_id,
        "orderid=E-1",
        "/sell/order/D-1",
    ):
        assert link in html
    # Sold, so neither List on link.
    assert "List&nbsp;on" not in html


def test_show_has_the_unsold_unit_links(client):
    unit = Unit(name="Plain")
    _add(unit)

    html = _html(client, unit)

    assert "List&nbsp;on&nbsp;eBay" in html
    assert url_for("EbayDraftModelView.from_unit", pk=unit.id) in html
    assert "/unitpictures/show/%d" % unit.id in html
    assert "/salesreceiptmodelview/show/" not in html
    assert "eBay:Order" not in html


def test_show_keeps_the_columns_it_had(client):
    unit = _full_unit()
    html = _html(client, unit)

    for label in ("Name", "Unit Type", "Discogs Release", "Purchase Lot", "Archived"):
        assert label in html


def test_the_rebox_show_and_the_pages_that_list_units_still_work(client):
    unit = _full_unit()

    rebox = client.get("/unitmodelreboxview/show/%d" % unit.id)
    lot = client.get("/storageboxmodelview/list/")

    assert rebox.status_code == 200
    assert "/unitpictures/show/%d" % unit.id in rebox.get_data(as_text=True)
    assert lot.status_code == 200
