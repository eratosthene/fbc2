"""
The home page's cards (#214): the same content in the same order as the page
before it was split, figures worked out only for the cards shown, and shared
figures worked out once.
"""

import dataclasses
import datetime

from auth import signed_in_client
from sqlalchemy import event

from fbc import db
from fbc.core import localtime
from fbc.models.discogs import DiscogsOrder
from fbc.models.expenses import Expense
from fbc.models.inventory import PurchaseLot, StorageBox, Unit
from fbc.models.sales import SalesReceipt
from fbc.models.supplies import Supply
from fbc.models.sync import SyncRecord
from fbc.sales import cards, index
from test_other_items import _order, _other
from test_tasks import _parked


def _unit(lot, box, sold=False, receipt=None, name="Unit"):
    unit = Unit(
        name=name,
        unit_type='12" LP',
        pressing="",
        grading="",
        purchase_lot=lot,
        storage_box=box,
        sold=sold,
        sales_receipt=receipt,
    )
    db.session.add(unit)
    return unit


def _rich_shop():
    """
    Listings synced, sales on eBay, on Discogs and with no order, a sold Other
    Item, an expense, a supply running low and a parked listing (so the To do
    banner has something in it).
    """
    lot = PurchaseLot(name="Crate Find", date=datetime.date(2024, 1, 1), price=100.0)
    box = StorageBox(name="Back Shelf")
    db.session.add_all([lot, box])
    ebay_order = _order(item_id="L1", order_id="E-1")
    discogs_order = DiscogsOrder(
        order_id="1-100",
        created_time=datetime.datetime(2024, 2, 1),
        status="Shipped",
        buyer="b",
        shipping=5.0,
        fee=1.0,
        total=25.0,
        currency="USD",
    )
    db.session.add(discogs_order)
    on_ebay = SalesReceipt(
        date=datetime.date(2024, 2, 1),
        sold_price=40.0,
        net_sold=35.0,
        ebay_orders=[ebay_order],
    )
    on_discogs = SalesReceipt(
        date=datetime.date(2024, 2, 2),
        sold_price=20.0,
        net_sold=19.0,
        discogs_orders=[discogs_order],
    )
    no_order = SalesReceipt(
        date=datetime.date(2024, 2, 3), sold_price=10.0, net_sold=9.0
    )
    db.session.add_all([on_ebay, on_discogs, no_order])
    for receipt in (on_ebay, on_discogs, no_order):
        _unit(lot, box, sold=True, receipt=receipt, name="Sold")
    _unit(lot, box, name="In stock")
    db.session.add(
        Expense(date=datetime.date(2024, 3, 1), category="other", amount=6.5)
    )
    db.session.add(Supply(name="LP Mailers", quantity=1, min_quantity=5))
    db.session.commit()
    _parked("P1")
    SyncRecord.record_finished(SyncRecord.LISTINGS_SYNC, localtime.utcnow())
    # An Other Item that sold, after the Units: its receipt must not reach them.
    item = _other(item_id="L9")
    other_receipt = SalesReceipt(
        date=datetime.date(2024, 2, 4),
        sold_price=25.0,
        net_sold=20.0,
        ebay_orders=[_order(item_id="L9", order_id="E-9")],
    )
    db.session.add(other_receipt)
    db.session.flush()
    item.sold = True
    item.sales_receipt_id = other_receipt.id
    db.session.commit()


def test_every_card_is_on_the_page_in_the_order_it_always_was(app):
    _rich_shop()

    with signed_in_client(app) as client:
        page = " ".join(client.get("/").get_data(as_text=True).split())

    in_order = [
        # The pinned alerts come first.
        "Time to reorder:",
        "LP Mailers",
        "To do:",
        "1 parked listing",
        # Then the cards.
        "<h2>Listings</h2>",
        "active on eBay.",
        "free insertions used this month",
        "waiting.",
        "Calibrate with the eBay app",
        "<h2>Stock</h2>",
        "Units in stock: <strong>2</strong>",
        "<h2>Year to date: 20",
        "Net sold:",
        "Reports:",
        "<h2>All time: Sales by marketplace</h2>",
        ">eBay</a>",
        ">Discogs</a>",
        ">Other</a>",
        "<h2>All Time: Profit &amp; Breakeven</h2>",
        "Net Profit: <strong>",
        "profit margin",
        "Breakeven per unit, including supplies",
        "Profit Details (by Purchase Lot and Storage Box)",
        "<td>Crate Find</td>",
        "<th>Storage Box</th>",
        "<td>Back Shelf</td>",
        "<h2>Other items</h2>",
        "Other items: <strong>1</strong> sold",
        "<h2>Money</h2>",
        "Supply cost:",
        "Other expenses: <strong>$6.50</strong>",
        "Expense Details",
        "Other (line 27a)",
    ]
    at = 0
    for text in in_order:
        found = page.find(text, at)
        assert found >= 0, (
            f"{text!r} is missing, or out of order: {page[at : at + 300]}"
        )
        at = found
    assert "<h2>All time</h2>" not in page
    assert page.count('class="home-card"') == len(cards.CARDS)


def test_the_cards_are_the_eight_in_the_owners_order():
    assert [card.key for card in cards.CARDS] == [
        "listings",
        "stock",
        "ytd",
        "marketplace_sales",
        "profit_breakeven",
        "other_items",
        "money",
    ]


def test_a_card_that_is_not_shown_never_works_out_its_figures(app):
    calls = []

    def spy(name):
        def figures(shared):
            calls.append(name)
            return {}

        return figures

    watched = [dataclasses.replace(card, figures=spy(card.key)) for card in cards.CARDS]
    shown = [c for c in watched if c.key in ("stock", "money")]

    context = index.home_context(shown)

    assert calls == ["stock", "money"]
    assert [s.card.key for s in context["cards"]] == ["stock", "money"]


def test_a_hidden_card_leaves_out_its_queries(app):
    _rich_shop()

    def count(shown):
        statements = []

        def record(*args, **kwargs):
            statements.append(args[2])

        db.session.expunge_all()
        event.listen(db.engine, "before_cursor_execute", record)
        try:
            index.home_context(shown)
        finally:
            event.remove(db.engine, "before_cursor_execute", record)
        return len(statements)

    everything = count(cards.CARDS)
    # Stock alone is one card of all-time figures: no Year to date, no listings
    # waiting, no marketplace table.
    assert count([cards.CARDS[1]]) < everything
    assert count([]) < count([cards.CARDS[1]])


def test_all_time_cards_share_one_set_of_figures(app, monkeypatch):
    _rich_shop()
    loads = []
    real = cards.inventory

    def counting(shared):
        loads.append(1)
        return real(shared)

    monkeypatch.setattr(cards, "inventory", counting)
    all_time_cards = [
        c for c in cards.CARDS if c.key in ("stock", "profit_breakeven", "money")
    ]
    cards.figures_for(all_time_cards)
    assert len(loads) == 1


# What the home page did before it was split into cards, counted on the same
# shop with the same test (database statements, leaving out Flask-AppBuilder's
# own permission and user lookups).
QUERIES_BEFORE_THE_CARDS = 57


def test_the_page_does_no_more_queries_than_before_it_was_cards(app):
    _rich_shop()
    client = signed_in_client(app)
    client.get("/")
    statements = []

    def record(conn, cursor, statement, *args):
        if "ab_" not in statement:
            statements.append(statement)

    db.session.expire_all()
    event.listen(db.engine, "before_cursor_execute", record)
    try:
        assert client.get("/").status_code == 200
    finally:
        event.remove(db.engine, "before_cursor_execute", record)

    assert len(statements) <= QUERIES_BEFORE_THE_CARDS


def test_listings_before_the_first_sync_says_no_data_and_links_to_the_sync(app):
    with signed_in_client(app) as client:
        page = " ".join(client.get("/").get_data(as_text=True).split())

    listings = page.split('id="card-listings"')[1].split('id="card-stock"')[0]
    assert "No data yet." in listings
    assert 'href="/syncebaylistings"' in listings
    assert "Calibrate" not in listings


def test_other_items_shows_nothing_until_one_has_sold(app):
    with signed_in_client(app) as client:
        page = client.get("/").get_data(as_text=True)

    assert 'id="card-other_items"' not in page
    assert "Other items" not in page
