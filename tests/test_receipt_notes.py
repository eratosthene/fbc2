"""
Sales Receipt Notes, the "Has no order" search, and the home page's row for
receipts with no order.
"""

import datetime

from auth import signed_in_client

from fbc import db
from fbc.models.discogs import DiscogsOrder
from fbc.models.ebay import EbayOrder
from fbc.models.sales import SalesReceipt
from fbc.sales import index
from types import SimpleNamespace

import pytest
from wtforms.validators import ValidationError

from fbc.views.sales import (
    FilterHasNoOrder,
    SalesReceiptModelView,
    not_both_marketplaces,
)

DAY = datetime.date(2026, 3, 1)
LINK = "/salesreceiptmodelview/list/?archived=all&_flt_0_ebay_orders=1"


def _receipt(notes=None, ebay=False, discogs=False, archived=False):
    receipt = SalesReceipt(
        date=DAY, sold_price=10.0, net_sold=9.0, notes=notes, archived=archived
    )
    if ebay:
        receipt.ebay_orders = [
            EbayOrder(
                order_id="E-1",
                created_time=datetime.datetime(2026, 3, 1),
                buyer="b",
                price=10.0,
                title="T",
                ebay_listing_id="1",
            )
        ]
    if discogs:
        receipt.discogs_orders = [
            DiscogsOrder(
                order_id="1-1",
                created_time=datetime.datetime(2026, 3, 1),
                status="New Order",
                buyer="b",
                shipping=1.0,
                fee=1.0,
                total=11.0,
                currency="USD",
            )
        ]
    db.session.add(receipt)
    db.session.commit()
    return receipt


def test_notes_are_kept_and_shown_on_forms_show_and_search(app):
    client = signed_in_client(app)
    receipt = _receipt(notes="sold in person to a friend")

    assert "sold in person to a friend" in client.get(
        f"/salesreceiptmodelview/show/{receipt.id}"
    ).get_data(as_text=True)
    assert 'name="notes"' in client.get("/salesreceiptmodelview/add").get_data(
        as_text=True
    )
    edit = client.get(f"/salesreceiptmodelview/edit/{receipt.id}").get_data(
        as_text=True
    )
    assert "sold in person to a friend" in edit
    assert "notes" in SalesReceiptModelView.search_columns


def test_has_no_order_leaves_only_receipts_with_no_order(app):
    client = signed_in_client(app)
    mine = _receipt(notes="friend")
    _receipt(notes="ebay one", ebay=True)
    _receipt(notes="discogs one", discogs=True)
    old = _receipt(notes="old friend", archived=True)

    filtered = FilterHasNoOrder("ebay_orders", SalesReceiptModelView.datamodel).apply(
        db.session.query(SalesReceipt), "1"
    )
    page = client.get(LINK)

    assert {r.id for r in filtered} == {mine.id, old.id}
    assert page.status_code == 200
    # archived=all: the home page counts archived receipts, so the list does.
    shown = page.get_data(as_text=True)
    assert f"/salesreceiptmodelview/show/{mine.id}" in shown
    assert f"/salesreceiptmodelview/show/{old.id}" in shown
    assert shown.count("/salesreceiptmodelview/show/") == 2


def test_the_has_no_order_filter_is_first_on_the_ebay_orders_column(app):
    filters = SalesReceiptModelView()._filters._search_filters["ebay_orders"]
    assert isinstance(filters[0], FilterHasNoOrder)


def test_home_lists_ebay_and_discogs_at_zero_and_links_the_no_order_row(app):
    client = signed_in_client(app)

    assert [(r["name"], r["receipts"]) for r in index.marketplace_sales()] == [
        ("eBay", 0),
        ("Discogs", 0),
    ]
    _receipt(notes="friend")
    _receipt(ebay=True)
    sales = {r["name"]: r for r in index.marketplace_sales()}
    html = client.get("/").get_data(as_text=True)

    assert sales[index.NO_ORDER_NAME]["receipts"] == 1
    assert sales["Discogs"]["receipts"] == 0
    assert ">Other</a>" in html
    assert "In person or not linked" not in html
    assert "payment processing fee" not in html
    assert "archived=all&amp;_flt_0_ebay_orders=1" in html
    assert "archived=all&amp;_flt_1_ebay_orders=1" in html
    assert "archived=all&amp;_flt_0_discogs_orders=1" in html


def test_has_an_order_filters_keep_only_receipts_of_that_marketplace(app):
    from fbc.views.sales import FilterHasDiscogsOrder, FilterHasEbayOrder

    from fbc import db
    from fbc.models.sales import SalesReceipt

    ebay = _receipt(ebay=True)
    other = _receipt(notes="friend")
    view = SalesReceiptModelView()
    query = db.session.query(SalesReceipt)

    kept = FilterHasEbayOrder("ebay_orders", view.datamodel).apply(query, "1").all()
    assert [r.id for r in kept] == [ebay.id]
    assert other.id not in [r.id for r in kept]
    assert (
        FilterHasDiscogsOrder("discogs_orders", view.datamodel).apply(query, "1").all()
        == []
    )
    # The home page links reach them by position.
    ebay_filters = view._filters._search_filters["ebay_orders"]
    assert isinstance(ebay_filters[1], FilterHasEbayOrder)
    assert isinstance(
        view._filters._search_filters["discogs_orders"][0], FilterHasDiscogsOrder
    )


def test_discogs_orders_list_says_the_payment_fee_is_missing(app):
    client = signed_in_client(app)
    html = client.get("/discogsordermodelview/list/").get_data(as_text=True)
    assert "payment processing fee" in html


def test_a_receipt_cant_have_both_an_ebay_and_a_discogs_order(app):
    def form(ebay):
        return SimpleNamespace(ebay_orders=SimpleNamespace(data=ebay))

    with pytest.raises(ValidationError, match="both an eBay order"):
        not_both_marketplaces(form(["e"]), SimpleNamespace(data=["d"]))
    # One kind, or none, is fine.
    not_both_marketplaces(form([]), SimpleNamespace(data=["d"]))
    not_both_marketplaces(form(["e"]), SimpleNamespace(data=[]))
    assert SalesReceiptModelView.validators_columns["discogs_orders"] == [
        not_both_marketplaces
    ]


def test_the_add_form_refuses_both_kinds_of_order(app):
    client = signed_in_client(app)
    ebay = _receipt(ebay=True).ebay_orders[0]
    discogs = _receipt(discogs=True).discogs_orders[0]
    before = db.session.query(SalesReceipt).count()
    page = client.post(
        "/salesreceiptmodelview/add",
        data={
            "ebay_orders": [str(ebay.order_id)],
            "discogs_orders": [str(discogs.order_id)],
            "date": "2026-03-02",
            "sold_price": "5",
            "net_sold": "4",
        },
    )
    assert "both an eBay order" in page.get_data(as_text=True)
    assert db.session.query(SalesReceipt).count() == before
