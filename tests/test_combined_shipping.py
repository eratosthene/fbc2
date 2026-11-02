"""Ship This for several Units and orders, and combining old receipts (#53)."""

import datetime
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

import fbc
from fbc import combine, db, listing, shipping
from fbc.index import home_totals
from fbc.models.ebay import EbayListing, EbayOrder, EbayOrderItem
from fbc.models.inventory import PurchaseLot, Unit
from fbc.models.sales import SalesReceipt, SalesReceiptLine, unit_weights
from fbc.models.supplies import SupplyUse
from fbc.util import add_ebay_listing, add_ebay_order
from test_listing import FakeHTTPResponse
from test_shipping import _shipping_supplies, client  # noqa: F401

WHEN = datetime.datetime(2026, 10, 28, 15, 30)


def _lot():
    lot = PurchaseLot(name="Lot", date=datetime.date(2026, 1, 1), price=10.0)
    db.session.add(lot)
    return lot


def _listing(item_id, price, units=1, lot=None):
    """A listing with `units` unsold Units on it."""
    found = EbayListing(
        item_id=item_id,
        title="Record " + item_id,
        price=price,
        url="https://ebay.com/itm/" + item_id,
    )
    db.session.add(found)
    for n in range(units):
        db.session.add(
            Unit(
                name="Record " + item_id + ("" if units == 1 else " #" + str(n)),
                unit_type='12" LP',
                pressing="",
                grading="",
                ebay_listing=found,
                purchase_lot=lot,
            )
        )
    return found


def _order(order_id, items, buyer="buyer1", when=WHEN, shipping=None):
    """An order of `items` ([(listing id, quantity, price)])."""
    order = EbayOrder(
        order_id=order_id,
        created_time=when,
        buyer=buyer,
        price=sum(q * p for _, q, p in items),
        shipping=shipping,
        title="Record " + items[0][0],
        ebay_listing_id=items[0][0],
        items=[
            EbayOrderItem(
                ebay_listing_id=lid, title="Record " + lid, quantity=q, price=p
            )
            for lid, q, p in items
        ],
    )
    db.session.add(order)
    db.session.commit()
    return order


def _sale(order_id, amount, items=1, when="2026-10-28T15:30:00.000Z"):
    return {
        "transactionType": "SALE",
        "bookingEntry": "CREDIT",
        "orderId": order_id,
        "transactionDate": when,
        "amount": {"value": f"{amount:.2f}"},
        "orderLineItems": [{"lineItemId": str(n)} for n in range(items)],
    }


def _label(order_id, amount, when="2026-10-29T10:00:00.000Z"):
    return {
        "transactionType": "SHIPPING_LABEL",
        "bookingEntry": "DEBIT",
        "orderId": order_id,
        "transactionDate": when,
        "amount": {"value": f"{amount:.2f}"},
    }


def _refund(order_id, amount, when="2026-10-29T11:00:00.000Z"):
    return {
        "transactionType": "REFUND",
        "bookingEntry": "DEBIT",
        "orderId": order_id,
        "transactionDate": when,
        "amount": {"value": f"{amount:.2f}"},
    }


@pytest.fixture
def finances(monkeypatch):
    """Answer Finances API calls with `finances.orders[order id]`'s transactions."""
    import requests

    monkeypatch.setattr(
        listing, "oauth_token", lambda scope=listing.MEDIA_SCOPE: "access-token"
    )
    state = SimpleNamespace(orders={}, asked=[])

    def fake_get(url, params=None, **kwargs):
        order_id = params["filter"][len("orderId:{") : -1]
        state.asked.append(order_id)
        transactions = state.orders.get(order_id, [])
        return FakeHTTPResponse(
            200, {"total": len(transactions), "transactions": transactions}
        )

    monkeypatch.setattr(requests, "get", fake_get)
    return state


# The order sync


def test_order_sync_keeps_every_item_and_the_shipping_paid(app):
    for item_id in ("111", "222"):
        add_ebay_listing(
            {
                "ItemID": item_id,
                "Title": "Record " + item_id,
                "BuyItNowPrice": {"value": "10.00"},
                "ListingDetails": {
                    "ViewItemURL": "https://ebay.com/itm/" + item_id,
                    "StartTime": "2026-10-01T10:00:00.000Z",
                },
            }
        )
    payload = {
        "OrderID": "O-1",
        "Subtotal": {"value": "26.00"},
        "BuyerUserID": "buyer42",
        "CreatedTime": "2026-10-28T10:00:00.000Z",
        "ShippingServiceSelected": {"ShippingServiceCost": {"value": "4.37"}},
        "TransactionArray": {
            "Transaction": [
                {
                    "Item": {"Title": "Record 111", "ItemID": "111"},
                    "QuantityPurchased": "1",
                    "TransactionPrice": {"value": "10.00"},
                },
                {
                    "Item": {"Title": "Record 222", "ItemID": "222"},
                    "QuantityPurchased": "2",
                    "TransactionPrice": {"value": "8.00"},
                },
            ]
        },
    }

    add_ebay_order(payload)

    order = db.session.get(EbayOrder, "O-1")
    assert (order.title, order.ebay_listing_id, order.shipping) == (
        "Record 111",
        "111",
        4.37,
    )
    assert [(i.ebay_listing_id, i.quantity, i.price) for i in order.items] == [
        ("111", 1, 10.0),
        ("222", 2, 8.0),
    ]
    assert order.listing_ids() == {"111": 1, "222": 2}


def test_order_sync_fills_in_items_on_an_order_it_already_had(app):
    _listing("111", 10.0)
    db.session.add(
        EbayOrder(
            order_id="O-1",
            created_time=WHEN,
            buyer="buyer42",
            price=10.0,
            title="Record 111",
            ebay_listing_id="111",
        )
    )
    db.session.commit()
    payload = {
        "OrderID": "O-1",
        "Subtotal": {"value": "10.00"},
        "BuyerUserID": "buyer42",
        "CreatedTime": "2026-10-28T10:00:00.000Z",
        "ShippingServiceSelected": {"ShippingServiceCost": {"value": "4.37"}},
        # ebaysdk gives a lone transaction as a dict.
        "TransactionArray": {
            "Transaction": {
                "Item": {"Title": "Record 111", "ItemID": "111"},
                "TransactionPrice": {"value": "10.00"},
            }
        },
    }

    add_ebay_order(payload)

    order = db.session.get(EbayOrder, "O-1")
    assert [(i.ebay_listing_id, i.quantity) for i in order.items] == [("111", 1)]
    assert order.shipping == 4.37


# Finding the Units


def test_an_order_of_several_units_finds_them_all(app):
    _listing("111", 10.0)
    _listing("222", 8.0, units=2)
    order = _order("O-1", [("111", 1, 10.0), ("222", 2, 8.0)])

    units, problems = shipping.shipment_units([order])

    assert problems == []
    assert sorted(u.name for u in units) == [
        "Record 111",
        "Record 222 #0",
        "Record 222 #1",
    ]
    assert shipping.can_ship(order)


def test_units_must_match_how_many_were_bought(app):
    _listing("111", 10.0)
    _listing("222", 8.0, units=3)
    order = _order("O-1", [("111", 1, 10.0), ("222", 2, 8.0)])

    units, problems = shipping.shipment_units([order])

    assert units is None
    assert problems == [
        "Listing 222 has 3 unsold Units on it, and 2 were bought.",
    ]
    assert not shipping.can_ship(order)


def test_an_order_synced_before_items_were_kept_uses_its_listing(app):
    _listing("111", 10.0)
    order = EbayOrder(
        order_id="O-1",
        created_time=WHEN,
        buyer="b",
        price=10.0,
        title="Record 111",
        ebay_listing_id="111",
    )
    db.session.add(order)
    db.session.commit()

    assert shipping.order_unit(order).name == "Record 111"


def test_combinable_orders_are_the_buyers_others_without_a_receipt(app):
    for item_id in ("1", "2", "3", "4", "5"):
        _listing(item_id, 5.0)
    first = _order("O-1", [("1", 1, 5.0)])
    second = _order("O-2", [("2", 1, 5.0)], when=WHEN + datetime.timedelta(hours=3))
    _order("O-3", [("3", 1, 5.0)], buyer="someone else")
    _order("O-4", [("4", 1, 5.0)], when=WHEN + datetime.timedelta(days=30))
    shipped = _order("O-5", [("5", 1, 5.0)])
    shipping.ship_order(shipped, shipping.shipment_units([shipped])[0], 4.0)

    assert shipping.combinable_orders(first) == [second]
    assert shipping.combinable_orders(second) == [first]


def test_order_list_offers_shipping_combined_or_alone(client):  # noqa: F811
    _listing("1", 5.0)
    _listing("2", 5.0)
    _listing("3", 5.0)
    first = _order("O-1", [("1", 1, 5.0)])
    _order("O-2", [("2", 1, 5.0)])
    alone = _order("O-3", [("3", 1, 5.0)], buyer="someone else")

    links = first.links()

    assert "Ship&nbsp;This&nbsp;Combined</a> (2&nbsp;orders)" in links
    assert "/ebayordermodelview/ship/O-1?combine=O-2" in links
    assert "Ship&nbsp;This&nbsp;Alone</a>" in links
    assert "Ship&nbsp;This</a>" in alone.links()
    assert "Combined" not in alone.links()


# Shipping one order of several Units


def test_shipping_an_order_of_several_units(client, finances):  # noqa: F811
    _listing("111", 10.0)
    _listing("222", 8.0, units=2)
    _order("O-1", [("111", 1, 10.0), ("222", 2, 8.0)])
    supplies = _shipping_supplies()
    finances.orders["O-1"] = [_sale("O-1", 22.40, items=2), _label("O-1", 5.13)]

    page = client.get("/ebayordermodelview/ship/O-1").get_data(as_text=True)
    assert "Record 222 #1" in page
    assert 'name="supply-' + str(supplies["sleeves 12"].id) + '" value="3"' in page
    assert "disabled>Ship</button>" not in page

    shipped = client.post(
        "/ebayordermodelview/ship/O-1",
        data={
            "net_sold": "17.27",
            "supply-" + str(supplies["mailers"].id): "1",
            "supply-" + str(supplies["sleeves 12"].id): "3",
        },
        follow_redirects=True,
    )

    assert "marked 3 Units sold" in shipped.get_data(as_text=True)
    db.session.expire_all()
    receipt = db.session.query(SalesReceipt).one()
    assert (receipt.order_ids(), receipt.sold_price, receipt.net_sold) == (
        ["O-1"],
        26.0,
        17.27,
    )
    assert all(u.sold and u.sales_receipt_id == receipt.id for u in Unit.query)
    assert (
        db.session.get(type(supplies["sleeves 12"]), supplies["sleeves 12"].id).quantity
        == 17
    )


# Shipping several orders together


def _two_orders():
    _listing("111", 10.0)
    _listing("222", 6.0)
    first = _order("O-1", [("111", 1, 10.0)], shipping=4.37)
    second = _order(
        "O-2", [("222", 1, 6.0)], shipping=4.37, when=WHEN + datetime.timedelta(hours=1)
    )
    return first, second


def test_shipment_earnings_add_up_every_orders(app, finances):
    _two_orders()
    finances.orders = {
        "O-1": [_sale("O-1", 12.30), _label("O-1", 5.13)],
        "O-2": [_sale("O-2", 8.50)],
    }

    earnings = shipping.shipment_earnings(["O-1", "O-2"])

    assert finances.asked == ["O-1", "O-2"]
    assert earnings["net"] == 15.67
    assert earnings["labels"] == 1
    assert {line["ebay_order_id"] for line in earnings["lines"]} == {"O-1", "O-2"}
    # One label for the package is what's expected.
    assert earnings["warnings"] == []


def test_shipment_earnings_need_a_sale_on_every_order(app, finances):
    _two_orders()
    finances.orders = {"O-1": [_sale("O-1", 12.30), _label("O-1", 5.13)]}

    earnings = shipping.shipment_earnings(["O-1", "O-2"])

    assert earnings["net"] is None
    assert "doesn't show a sale for order O-2" in earnings["warnings"][0]


def test_combined_discount_is_shipping_paid_less_the_label_and_refunds(app, finances):
    first, second = _two_orders()
    finances.orders = {
        "O-1": [_sale("O-1", 12.30), _label("O-1", 5.13)],
        "O-2": [_sale("O-2", 8.50)],
    }
    earnings = shipping.shipment_earnings(["O-1", "O-2"])

    assert shipping.combined_discount([first, second], earnings) == {
        "paid": 8.74,
        "label": 5.13,
        "refunded": 0,
        "discount": 3.61,
    }

    finances.orders["O-2"].append(_refund("O-2", 3.61))
    earnings = shipping.shipment_earnings(["O-1", "O-2"])
    assert shipping.combined_discount([first, second], earnings)["discount"] == 0
    assert shipping.combined_discount([first], earnings) is None


def test_shipping_two_orders_together_records_one_receipt(client, finances):  # noqa: F811
    _two_orders()
    finances.orders = {
        "O-1": [_sale("O-1", 12.30), _label("O-1", 5.13)],
        "O-2": [_sale("O-2", 8.50)],
    }

    alone = client.get("/ebayordermodelview/ship/O-1").get_data(as_text=True)
    assert 'name="combine" value="O-2"' in alone
    assert "Refund\n$3.61" not in alone
    page = client.get("/ebayordermodelview/ship/O-1?combine=O-2").get_data(as_text=True)
    assert "paid $8.74 for shipping" in page
    assert "refund\n$3.61 of shipping" in page
    assert '<input type="hidden" name="combine" value="O-2">' in page

    shipped = client.post(
        "/ebayordermodelview/ship/O-1",
        data={"net_sold": "15.67", "combine": "O-2"},
        follow_redirects=True,
    )

    assert "for 2 orders and marked 2 Units sold" in shipped.get_data(as_text=True)
    db.session.expire_all()
    receipt = db.session.query(SalesReceipt).one()
    assert receipt.order_ids() == ["O-1", "O-2"]
    assert (receipt.date, receipt.sold_price, receipt.net_sold) == (
        datetime.date(2026, 10, 28),
        16.0,
        15.67,
    )
    assert [line.kind for line in receipt.lines] == ["SALE", "SALE", "SHIPPING_LABEL"]
    assert all(u.sales_receipt_id == receipt.id for u in Unit.query)
    assert db.session.get(EbayOrder, "O-2").sales_receipts == [receipt]


def test_refresh_earnings_asks_about_every_order_on_the_receipt(client, finances):  # noqa: F811
    first, second = _two_orders()
    units = shipping.shipment_units([first, second])[0]
    receipt = shipping.ship_order([first, second], units, 15.67)
    finances.orders = {
        "O-1": [_sale("O-1", 12.30), _label("O-1", 5.13)],
        "O-2": [_sale("O-2", 8.50), _refund("O-2", 3.61)],
    }

    page = client.get("/salesreceiptmodelview/earnings/" + str(receipt.id))

    html = page.get_data(as_text=True)
    assert finances.asked == ["O-1", "O-2"]
    assert "Orders</th>" in html
    assert "$12.06" in html


def test_backfill_earnings_fills_in_a_receipt_of_several_orders(app, finances):
    first, second = _two_orders()
    units = shipping.shipment_units([first, second])[0]
    receipt = shipping.ship_order([first, second], units, 15.67)
    finances.orders = {
        "O-1": [_sale("O-1", 12.30), _label("O-1", 5.13)],
        "O-2": [_sale("O-2", 8.50)],
    }

    counts = shipping.backfill_earnings(apply=True, echo=lambda _: None)

    assert counts["saved"] == 1
    db.session.expire_all()
    assert db.session.get(SalesReceipt, receipt.id).lines_total() == 15.67


# Splitting a receipt between its Units


def test_unit_weights():
    units = [(1, "a", None, None), (2, "b", None, None)]
    assert unit_weights(units, {"a": 20.0, "b": 10.0}, {}) == {1: 20.0, 2: 10.0}
    assert unit_weights(units, {"a": 20.0}, {"b": 5.0}) == {1: 20.0, 2: 5.0}
    assert unit_weights(units, {"a": 20.0}, {}) == {1: 20.0, 2: 20.0}
    assert unit_weights(units, {}, {}) is None
    # Retail Price, for a Unit with no listing, before the average.
    assert unit_weights(
        [(1, "a", None, None), (2, None, None, 8.0)], {"a": 20.0}, {}
    ) == {
        1: 20.0,
        2: 8.0,
    }
    assert unit_weights([(1, "a", None, None)], {"a": 20.0}, {}) is None
    assert unit_weights(
        [(1, "a", 3.0, None), (2, "b", None, None)], {"a": 20.0}, {"b": 5.0}
    ) == {
        1: 3.0,
        2: 5.0,
    }


def test_home_page_splits_a_receipt_by_what_each_unit_sold_for(app):
    lot = _lot()
    other_lot = PurchaseLot(name="Other", date=datetime.date(2026, 1, 1), price=5.0)
    db.session.add(other_lot)
    _listing("111", 30.0, lot=lot)
    _listing("222", 10.0, lot=other_lot)
    order = _order("O-1", [("111", 1, 20.0), ("222", 1, 5.0)])
    receipt = shipping.ship_order(order, shipping.shipment_units([order])[0], 20.0)

    totals = home_totals()

    assert totals["totals"]["gross"] == 25.0
    assert totals["lot_totals"][lot.id]["gross"] == 20.0
    assert totals["lot_totals"][lot.id]["net"] == 16.0
    assert totals["lot_totals"][other_lot.id]["gross"] == 5.0
    unit = db.session.query(Unit).filter_by(ebay_listing_id="222").one()
    assert unit.sold_for() == 5.0
    assert receipt.unit_share(unit, receipt.net_sold) == 4.0


# Combining old receipts


def _split_by_hand():
    """An order of two Units recorded as a receipt per Unit, as before #53."""
    lot = _lot()
    _listing("111", 30.0, lot=lot)
    _listing("222", 10.0, lot=lot)
    order = EbayOrder(
        order_id="O-1",
        created_time=WHEN,
        buyer="b",
        price=25.0,
        title="Record 111",
        ebay_listing_id="111",
    )
    db.session.add(order)
    receipts = []
    for listing_id, sold, net in (("111", 20.0, 16.0), ("222", 5.0, 3.5)):
        receipt = SalesReceipt(
            date=WHEN.date(), ebay_orders=[order], sold_price=sold, net_sold=net
        )
        unit = db.session.query(Unit).filter_by(ebay_listing_id=listing_id).one()
        unit.sold = True
        unit.sales_receipt = receipt
        db.session.add(receipt)
        receipts.append(receipt)
    db.session.flush()
    db.session.add(
        SupplyUse(
            supply=_shipping_supplies()["mailers"],
            count=1,
            kind="shipping",
            sales_receipt=receipts[1],
        )
    )
    db.session.commit()
    return order, receipts


def test_receipts_sharing_an_order_are_found_and_combined(app):
    order, receipts = _split_by_hand()
    before = home_totals()

    groups = combine.receipts_to_combine()
    assert [(why, [r.id for r in rs]) for why, rs in groups] == [
        ("same order", [r.id for r in receipts])
    ]
    kept = combine.combine_receipts(groups[0][1])
    db.session.commit()

    assert db.session.query(SalesReceipt).all() == [kept]
    assert (kept.order_ids(), kept.sold_price, kept.net_sold) == (["O-1"], 25.0, 19.5)
    assert {u.sales_receipt_id for u in Unit.query} == {kept.id}
    assert db.session.query(SupplyUse).one().sales_receipt_id == kept.id
    # What each Unit sold for is kept on it.
    assert sorted(u.sold_price for u in Unit.query) == [5.0, 20.0]
    after = home_totals()
    for key in ("gross", "net", "profit"):
        assert round(after["totals"][key], 2) == round(before["totals"][key], 2)
    assert kept.order_receipts() == 1


def _package_by_hand():
    """Two orders in one package, a receipt each: the second has no label."""
    first, second = _two_orders()
    receipts = []
    for order, lines in (
        (first, [("SALE", 12.30), ("SHIPPING_LABEL", -5.13)]),
        (second, [("SALE", 8.50), ("REFUND", -3.61)]),
    ):
        unit = (
            db.session.query(Unit)
            .filter_by(ebay_listing_id=order.ebay_listing_id)
            .one()
        )
        receipt = SalesReceipt(
            date=order.created_time.date(),
            ebay_orders=[order],
            sold_price=order.price,
            net_sold=round(sum(a for _, a in lines), 2),
            lines=[
                SalesReceiptLine(
                    kind=kind,
                    label=kind,
                    amount=amount,
                    ebay_order_id=order.order_id,
                    transaction_id=order.order_id + kind,
                )
                for kind, amount in lines
            ],
        )
        unit.sold = True
        unit.sales_receipt = receipt
        db.session.add(receipt)
        receipts.append(receipt)
    db.session.commit()
    return receipts


def test_a_package_of_orders_is_found_by_its_label(app):
    receipts = _package_by_hand()
    # Another buyer's order with no label, nowhere near a labelled one.
    _listing("333", 5.0)
    lone = _order("O-3", [("333", 1, 5.0)], buyer="someone else")
    db.session.add(
        SalesReceipt(
            date=WHEN.date(),
            ebay_orders=[lone],
            sold_price=5.0,
            net_sold=4.0,
            lines=[SalesReceiptLine(kind="SALE", label="Sale", amount=4.0)],
        )
    )
    db.session.commit()

    groups = combine.receipts_to_combine()
    assert [(why, [r.id for r in rs]) for why, rs in groups] == [
        ("same package", [r.id for r in receipts])
    ]

    kept = combine.combine_receipts(groups[0][1])
    db.session.commit()

    assert kept.order_ids() == ["O-1", "O-2"]
    assert (kept.sold_price, kept.net_sold) == (16.0, 12.06)
    assert sorted(line.kind for line in kept.lines) == [
        "REFUND",
        "SALE",
        "SALE",
        "SHIPPING_LABEL",
    ]
    assert kept.lines_total() == kept.net_sold


def test_receipts_of_orders_with_labels_each_are_left_apart(app):
    first, second = _two_orders()
    for order in (first, second):
        db.session.add(
            SalesReceipt(
                date=WHEN.date(),
                ebay_orders=[order],
                sold_price=order.price,
                net_sold=5.0,
                lines=[
                    SalesReceiptLine(kind="SALE", label="Sale", amount=10.0),
                    SalesReceiptLine(kind="SHIPPING_LABEL", label="Label", amount=-5.0),
                ],
            )
        )
    db.session.commit()

    assert combine.receipts_to_combine() == []


def test_combine_receipts_command_only_shows_without_apply(app):
    _split_by_hand()
    runner = CliRunner()

    shown = runner.invoke(fbc.app.cli, ["combine-receipts"], catch_exceptions=False)

    assert "Same order: " in shown.output
    assert "Home page totals:" in shown.output
    assert "Sold For: $25.00\n" in shown.output
    assert "Nothing was saved" in shown.output
    assert db.session.query(SalesReceipt).count() == 2

    applied = runner.invoke(
        fbc.app.cli, ["combine-receipts", "--apply"], catch_exceptions=False
    )
    assert "Combined 1 groups." in applied.output
    db.session.expire_all()
    assert db.session.query(SalesReceipt).count() == 1


def test_combine_receipts_command_can_combine_chosen_receipts(app):
    receipts = _package_by_hand()
    ids = ",".join(str(r.id) for r in receipts)

    result = CliRunner().invoke(
        fbc.app.cli, ["combine-receipts", "--receipts", ids, "--apply"]
    )

    assert "Chosen: " in result.output
    db.session.expire_all()
    assert db.session.query(SalesReceipt).one().order_ids() == ["O-1", "O-2"]


def test_combine_action_on_the_sales_receipts_list(client, finances):  # noqa: F811
    receipts = _package_by_hand()

    page = client.post(
        "/salesreceiptmodelview/action_post",
        data={"action": "combine", "rowid": [str(r.id) for r in receipts]},
        follow_redirects=True,
    )

    html = page.get_data(as_text=True)
    assert "Combined 2 Sales Receipts into this one" in html
    db.session.expire_all()
    assert db.session.query(SalesReceipt).one().order_ids() == ["O-1", "O-2"]


def test_combine_action_needs_two_receipts(client, finances):  # noqa: F811
    receipts = _package_by_hand()

    page = client.post(
        "/salesreceiptmodelview/action_post",
        data={"action": "combine", "rowid": [str(receipts[0].id)]},
        follow_redirects=True,
    )

    assert "Select two or more" in page.get_data(as_text=True)
    assert db.session.query(SalesReceipt).count() == 2
