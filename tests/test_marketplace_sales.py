"""
Slice 4 of the Discogs Marketplace: Ship This for Discogs orders, ending the
other marketplace's listing when a Unit sells, and Discogs sales on the home
page, Sales Receipts and Tasks.
"""

import datetime
import logging

import pytest
from fake_discogs import FakeAccount, FakeDiscogs, api_error, failing

import fbc
from fbc import db
from fbc.core import devmode, localtime
from fbc.discogs.client import DiscogsAuthError
from fbc.ebay import publish
from fbc.models.discogs import DiscogsListing, DiscogsOrder, DiscogsOrderItem
from fbc.models.ebay import EbayDraft, EbayListing, EbayOrder, EbayOrderItem
from fbc.models.inventory import Unit
from fbc.models.sales import (
    DISCOGS_FEE,
    FAILED,
    ListingEnd,
    SalesReceipt,
    earnings_totals,
)
from fbc.sales import cards, ship, tasks
from fbc.sync import ending, jobs
from test_listing import _carnaval


@pytest.fixture(autouse=True)
def info_logs(caplog):
    caplog.set_level(logging.INFO)


@pytest.fixture
def client():
    sm = fbc.appbuilder.sm
    sm.create_db()
    fbc.appbuilder.add_permissions(update_perms=True)
    sm.add_user("admin", "A", "Dmin", "admin@example.com", sm.find_role("Admin"), "pw")
    client = fbc.app.test_client()
    client.post("/login/", data={"username": "admin", "password": "pw"})
    return client


@pytest.fixture
def discogs(monkeypatch):
    fake = FakeAccount()
    FakeDiscogs(account=fake).install(monkeypatch)
    return fake


@pytest.fixture
def ebay_end(monkeypatch):
    """Record eBay's end calls; set `.answer` to (ok, message)."""
    calls = []
    answer = {"value": (True, "Ended on eBay.")}

    def end(item_id):
        calls.append(item_id)
        return answer["value"]

    monkeypatch.setattr(publish, "end_listing", end)
    end.calls = calls
    end.answer = answer
    return end


SOLD = datetime.datetime(2026, 11, 10, 12, 0)
LISTED = datetime.datetime(2026, 11, 1, 12, 0)


def _unit(**kw):
    unit = _carnaval(**kw)
    return db.session.get(Unit, unit.id)


def _plain_unit(**kw):
    """Another Unit, with no release: _carnaval can only be made once."""
    unit = Unit(name="Other", unit_type='12" LP', pressing="", grading="", **kw)
    db.session.add(unit)
    db.session.commit()
    return unit


def _discogs_listing(unit, listing_id=7001, status="For Sale", posted=LISTED):
    row = DiscogsListing(
        listing_id=listing_id,
        release_id=1,
        unit_id=unit.id,
        status=status,
        posted=posted,
        ended=False,
        price=20.0,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _ebay_listing(item_id="L1", start=LISTED):
    row = EbayListing(
        item_id=item_id,
        title="T",
        price=25.0,
        url="https://x/" + item_id,
        start_time=start,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _ebay_order(item_id="L1", created=SOLD, quantity=1):
    order = EbayOrder(
        order_id="E-" + item_id,
        created_time=created,
        buyer="b",
        price=25.0,
        title="T",
        ebay_listing_id=item_id,
        items=[EbayOrderItem(ebay_listing_id=item_id, quantity=quantity, price=25.0)],
    )
    db.session.add(order)
    db.session.commit()
    return order


def _discogs_order(
    unit, order_id="1-100", status="New Order", created=SOLD, price=19.0
):
    order = DiscogsOrder(
        order_id=order_id,
        created_time=created,
        status=status,
        buyer="dbuyer",
        shipping=5.0,
        fee=1.92,
        total=price + 5.0,
        currency="USD",
        items=[
            DiscogsOrderItem(
                listing_id=7001,
                release_id=1,
                title="Carnaval",
                price=price,
                unit_id=unit.id,
            )
        ],
    )
    db.session.add(order)
    db.session.commit()
    return order


# Ship This for a Discogs order


def test_earnings_are_the_total_and_discogs_fee_and_nothing_else(app):
    order = _discogs_order(_unit())

    lines, net = ship.discogs_earnings(order)

    assert [(x["kind"], x["amount"]) for x in lines] == [("SALE", 24.0), ("FEE", -1.92)]
    assert lines[1]["fee_type"] == DISCOGS_FEE
    assert net == order.net() == 22.08


def test_units_must_be_linked_and_unsold(app):
    unit = _unit()
    order = _discogs_order(unit)
    assert ship.discogs_shipment_units(order) == ([unit], [])

    order.items[0].unit_id = None
    units, problems = ship.discogs_shipment_units(order)
    assert units is None and "isn't linked to a Unit" in problems[0]

    order.items[0].unit_id = unit.id
    unit.sold = True
    units, problems = ship.discogs_shipment_units(order)
    assert units is None and "already sold" in problems[0]


def test_the_ship_page_shows_the_earnings_and_says_what_isnt_known(client, discogs):
    _discogs_order(_unit())

    html = client.get("/discogsordermodelview/ship/1-100").get_data(as_text=True)

    assert 'value="22.08"' in html
    assert "payment processing fee" in html
    assert "Discogs selling fee" in html


def test_shipping_records_the_receipt_and_marks_the_unit_sold(client, discogs):
    unit = _unit()
    order = _discogs_order(unit)

    page = client.post("/discogsordermodelview/ship/1-100", data={"net_sold": "20.50"})

    assert page.status_code == 302
    db.session.expire_all()
    receipt = db.session.query(SalesReceipt).one()
    assert receipt.discogs_orders == [order]
    assert receipt.marketplace() == "Discogs"
    assert (receipt.sold_price, receipt.net_sold) == (19.0, 20.5)
    assert receipt.date == datetime.date(2026, 11, 10)
    # 24.00 - 1.92 = 22.08 in the lines; Net Sold 20.50 leaves an adjustment.
    assert [(x.kind, x.amount) for x in receipt.lines] == [
        ("SALE", 24.0),
        ("FEE", -1.92),
        ("MANUAL", -1.58),
    ]
    assert db.session.get(Unit, unit.id).sold is True
    # The order isn't written back to Discogs.
    assert discogs.names() == []
    assert earnings_totals()["discogs_fees"] == -1.92

    again = client.get("/discogsordermodelview/ship/1-100")
    assert again.status_code == 302
    assert db.session.query(SalesReceipt).count() == 1


def test_shipping_needs_a_net_sold_and_ignores_a_cancelled_order(client, discogs):
    _discogs_order(_unit())
    client.post("/discogsordermodelview/ship/1-100", data={"net_sold": "lots"})
    assert db.session.query(SalesReceipt).count() == 0

    cancelled = _discogs_order(
        _plain_unit(), "1-200", status="Cancelled (Non-Paying Buyer)"
    )
    page = client.get("/discogsordermodelview/ship/1-200")
    assert page.status_code == 302 and not cancelled.sales_receipts


def test_an_order_with_an_unlinked_item_says_how_to_ship_by_hand(client, discogs):
    order = _discogs_order(_unit())
    order.items[0].unit_id = None
    db.session.commit()

    html = client.get(
        "/discogsordermodelview/ship/1-100", follow_redirects=True
    ).get_data(as_text=True)

    assert "ship it by hand" in html


def test_the_ship_page_warns_about_a_listing_still_up_on_the_other_site(
    client, discogs
):
    unit = _unit(ebay_listing=_ebay_listing())
    _discogs_order(unit)

    html = client.get("/discogsordermodelview/ship/1-100").get_data(as_text=True)

    assert "still has an active eBay listing" in html


def test_the_receipt_and_list_pages_show_discogs_orders(client, discogs):
    unit = _unit()
    _discogs_order(unit)
    client.post("/discogsordermodelview/ship/1-100", data={"net_sold": "22.08"})
    receipt = db.session.query(SalesReceipt).one()

    show = client.get("/salesreceiptmodelview/show/%d" % receipt.id).get_data(
        as_text=True
    )
    sales = client.get("/salesreceiptmodelview/list/").get_data(as_text=True)
    orders = client.get("/discogsordermodelview/list/").get_data(as_text=True)

    assert "payment processing fee" in show
    assert "Discogs:Order" in sales
    assert "Sales&nbsp;Receipt" in orders and "Ship&nbsp;This" not in orders


# Ending the other listing


def test_an_ebay_sale_deletes_the_discogs_listing(app, discogs):
    unit = _unit(ebay_listing=_ebay_listing())
    _discogs_listing(unit)
    _ebay_order()

    [row] = ending.end_sold_elsewhere()

    assert discogs.calls_to("delete_listing") == [(7001,)]
    assert (row.status, row.ended_on, row.sold_on) == ("ended", "Discogs", "eBay")
    assert (row.order_id, row.unit_id) == ("E-L1", unit.id)
    assert db.session.get(DiscogsListing, 7001).ended is True
    # A second pass has nothing left to do.
    assert ending.end_sold_elsewhere() == []
    assert len(discogs.calls_to("delete_listing")) == 1


def test_a_discogs_draft_is_deleted_too(app, discogs):
    unit = _unit(ebay_listing=_ebay_listing())
    _discogs_listing(unit, status="Draft")
    _ebay_order()

    ending.end_sold_elsewhere()

    assert discogs.calls_to("delete_listing") == [(7001,)]


def test_a_discogs_sale_ends_the_ebay_listing(app, discogs, ebay_end):
    unit = _unit(ebay_listing=_ebay_listing())
    _discogs_order(unit)

    [row] = ending.end_sold_elsewhere()

    assert ebay_end.calls == ["L1"]
    assert (row.status, row.ended_on, row.sold_on) == ("ended", "eBay", "Discogs")
    assert db.session.get(EbayListing, "L1").ended is True


def test_a_discogs_sale_deletes_an_unpublished_ebay_draft(app, discogs, ebay_end):
    unit = _unit()
    db.session.add(EbayDraft(unit_id=unit.id, title="Draft title", status="draft"))
    db.session.commit()
    _discogs_order(unit, created=localtime.utcnow())

    [row] = ending.end_sold_elsewhere()

    assert db.session.query(EbayDraft).count() == 0
    assert ebay_end.calls == []
    assert row.listing_ref.startswith("draft ") and row.status == "ended"


def test_a_cancelled_discogs_order_ends_nothing(app, discogs, ebay_end):
    unit = _unit(ebay_listing=_ebay_listing())
    _discogs_order(unit, status="Cancelled (Non-Paying Buyer)")

    assert ending.end_sold_elsewhere() == []
    assert ebay_end.calls == []


def test_a_sale_from_before_the_other_listing_ends_nothing(app, discogs):
    unit = _unit(ebay_listing=_ebay_listing())
    _discogs_listing(unit, posted=SOLD + datetime.timedelta(days=1))
    _ebay_order()

    assert ending.end_sold_elsewhere() == []
    assert discogs.names() == []


def test_a_listing_with_other_units_unsold_is_left_alone(app, discogs):
    listing = _ebay_listing()
    unit = _unit(ebay_listing=listing)
    other = _plain_unit(ebay_listing=listing)
    _discogs_listing(unit)
    _ebay_order(quantity=1)

    assert unit.id != other.id
    assert ending.end_sold_elsewhere() == []


def test_a_refusal_is_kept_as_a_failed_end_and_tried_again(app, discogs, caplog):
    unit = _unit(ebay_listing=_ebay_listing())
    _discogs_listing(unit)
    _ebay_order()
    discogs.fail = failing("delete_listing", api_error(403, "Not yours"))

    [row] = ending.end_sold_elsewhere()

    assert row.status == FAILED and "Not yours" in row.message
    assert db.session.get(DiscogsListing, 7001).ended is False
    assert tasks.counts()["failed_endings"] == 1
    assert "Discogs listing 7001" in caplog.text

    discogs.fail = None
    [again] = ending.end_sold_elsewhere()
    assert again.id == row.id and again.status == "ended"
    assert tasks.counts()["failed_endings"] == 0


def test_not_connected_to_discogs_is_a_failed_end_too(app, discogs):
    unit = _unit(ebay_listing=_ebay_listing())
    _discogs_listing(unit)
    _ebay_order()
    discogs.fail = failing("delete_listing", DiscogsAuthError("not connected"))

    [row] = ending.end_sold_elsewhere()

    assert row.status == FAILED and "not connected" in row.message


def test_a_listing_already_gone_counts_as_ended(app, discogs):
    unit = _unit(ebay_listing=_ebay_listing())
    _discogs_listing(unit)
    _ebay_order()
    discogs.fail = failing("delete_listing", api_error(404))

    [row] = ending.end_sold_elsewhere()

    assert row.status == "ended" and "already gone" in row.message


def test_an_ebay_refusal_is_kept_and_clears_when_the_listing_ends_itself(
    app, discogs, ebay_end
):
    unit = _unit(ebay_listing=_ebay_listing())
    _discogs_order(unit)
    ebay_end.answer["value"] = (False, "eBay is down")

    [row] = ending.end_sold_elsewhere()
    assert row.status == FAILED

    db.session.get(EbayListing, "L1").ended = True
    db.session.commit()
    ending.end_sold_elsewhere()
    assert db.session.get(ListingEnd, row.id).status == "ended"
    assert tasks.counts()["failed_endings"] == 0


def test_the_orders_syncs_end_listings_and_survive_a_failure(app, discogs, monkeypatch):
    unit = _unit(ebay_listing=_ebay_listing())
    _discogs_listing(unit)
    _ebay_order()
    discogs.order_items = []

    jobs.sync_discogs_orders()
    assert discogs.calls_to("delete_listing") == [(7001,)]

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(ending, "end_sold_elsewhere", boom)
    jobs.sync_discogs_orders()  # logs it, doesn't raise


def test_dev_mode_only_logs(app, discogs, ebay_end, monkeypatch, caplog):
    unit = _unit(ebay_listing=_ebay_listing())
    _discogs_listing(unit)
    _ebay_order()
    monkeypatch.setattr(devmode, "on", lambda: True)

    assert ending.end_sold_elsewhere() == []
    assert discogs.names() == [] and ebay_end.calls == []
    assert "Dev Mode is turned on" in caplog.text


def test_the_deleted_listing_call_is_refused_in_dev_mode(app, monkeypatch):
    monkeypatch.setattr(devmode, "on", lambda: True)
    from fbc.discogs import client

    with pytest.raises(DiscogsAuthError):
        client.account().delete_listing(1)


# Home page and Tasks


def test_tasks_count_discogs_orders_to_ship(app):
    unit = _unit()
    _discogs_order(unit)
    _discogs_order(_plain_unit(), "1-200", status="Cancelled (Seller)")
    shipped = _discogs_order(_plain_unit(), "1-300")
    shipped.sales_receipts = [
        SalesReceipt(date=SOLD.date(), sold_price=1.0, net_sold=1.0)
    ]
    db.session.commit()

    assert tasks.counts()["discogs_orders"] == 1


def test_the_home_page_shows_discogs_sales_and_failed_endings(client, discogs):
    unit = _unit()
    _discogs_order(unit)
    client.post("/discogsordermodelview/ship/1-100", data={"net_sold": "22.08"})
    db.session.add(
        ListingEnd(
            unit_id=unit.id,
            sold_on="eBay",
            ended_on="Discogs",
            listing_ref="7001",
            status=FAILED,
            message="no",
            created=SOLD,
            updated=SOLD,
        )
    )
    db.session.commit()

    sales = cards.marketplace_sales()
    html = client.get("/").get_data(as_text=True)

    assert sales == [
        {"name": "eBay", "receipts": 0, "gross": 0.0, "net": 0.0, "no_order": False},
        {
            "name": "Discogs",
            "receipts": 1,
            "gross": 19.0,
            "net": 22.08,
            "no_order": False,
        },
    ]
    assert "Sales by marketplace" in html
    assert "payment processing fee" not in html
    assert "Sold twice?" in html
    assert client.get("/listingendmodelview/list/").status_code == 200
    assert client.get("/failedendingsview/list/").status_code == 200
    assert client.get("/discogsorderstoshipview/list/").status_code == 200


def test_shipping_a_discogs_order_goes_back_to_the_list_it_came_from(client, discogs):
    _discogs_order(_unit())
    list_url = (
        "/discogsordersToshipview/list/".lower() + "?psize_DiscogsOrdersToShipView=50"
    )
    client.get(list_url)

    response = client.post(
        "/discogsordersToshipview/ship/1-100".lower(), data={"net_sold": "20.50"}
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith(list_url)
    assert db.session.query(SalesReceipt).count() == 1


def test_a_problem_shipping_a_discogs_order_stays_on_its_page(client, discogs):
    _discogs_order(_unit())

    response = client.post(
        "/discogsordermodelview/ship/1-100", data={"net_sold": "lots"}
    )

    assert response.status_code == 200
