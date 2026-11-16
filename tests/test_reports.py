import datetime

import pytest
from auth import signed_in_client

from fbc import db
from fbc.models.expenses import Expense
from fbc.models.discogs import DiscogsOrder
from fbc.models.ebay import EbayOrder
from fbc.models.inventory import PurchaseLot, Unit
from fbc.models.other import OtherItem
from fbc.models.sales import SalesReceipt
from fbc.models.supplies import PurchaseOrder
from fbc.sales import index, reports

D = datetime.date


def _lot(price, day):
    lot = PurchaseLot(name="Lot", date=day, price=price)
    db.session.add(lot)
    db.session.commit()
    return lot


def _sale(lot, gross, net, day, sold=True, order=None):
    receipt = SalesReceipt(date=day, sold_price=gross, net_sold=net)
    if order:
        receipt.ebay_orders.append(order) if isinstance(
            order, EbayOrder
        ) else receipt.discogs_orders.append(order)
    db.session.add(receipt)
    unit = Unit(
        name="U",
        unit_type='12" LP',
        pressing="",
        grading="",
        purchase_lot=lot,
        sold=sold,
        sales_receipt=receipt,
    )
    db.session.add(unit)
    db.session.commit()
    return receipt


def _args(**kw):
    return kw


def test_presets():
    now = D(2026, 5, 17)
    assert reports.preset_range("month", now) == (D(2026, 5, 1), D(2026, 5, 31))
    assert reports.preset_range("lastmonth", now) == (D(2026, 4, 1), D(2026, 4, 30))
    assert reports.preset_range("quarter", now) == (D(2026, 4, 1), now)
    assert reports.preset_range("ytd", now) == (D(2026, 1, 1), now)
    assert reports.preset_range("lastyear", now) == (D(2025, 1, 1), D(2025, 12, 31))
    assert reports.preset_range("12months", now) == (D(2025, 5, 18), now)
    assert reports.preset_range("all", now) == (None, None)


def test_period_from_the_url():
    p = reports.Period({"from": "2026-01-01", "to": "2026-06-30", "group": "quarter"})
    assert (p.start, p.end, p.group, p.preset) == (
        D(2026, 1, 1),
        D(2026, 6, 30),
        "quarter",
        "",
    )
    assert p.query() == {"group": "quarter", "from": "2026-01-01", "to": "2026-06-30"}
    bad = reports.Period({"from": "nonsense"}, now=D(2026, 5, 17))
    assert bad.notice and bad.preset == "ytd"
    backwards = reports.Period({"from": "2026-02-01", "to": "2026-01-01"})
    assert backwards.notice
    assert reports.Period({"period": "huh"}).preset == "ytd"
    assert reports.Period({"group": "week"}).group == "month"


def test_buckets_by_month_and_quarter():
    months = reports.buckets(D(2026, 1, 15), D(2026, 3, 10), "month")
    assert months == [
        ("2026-01", D(2026, 1, 15), D(2026, 1, 31)),
        ("2026-02", D(2026, 2, 1), D(2026, 2, 28)),
        ("2026-03", D(2026, 3, 1), D(2026, 3, 10)),
    ]
    quarters = reports.buckets(D(2025, 11, 1), D(2026, 4, 1), "quarter")
    assert [q[0] for q in quarters] == ["2025 Q4", "2026 Q1", "2026 Q2"]


def test_profit_and_loss_for_a_period(app):
    lot = _lot(100.0, D(2026, 1, 5))
    # Two Units in the Lot, one sold in January, one in March.
    _sale(lot, 40.0, 34.0, D(2026, 1, 20), order=EbayOrder(order_id="1"))
    _sale(lot, 60.0, 50.0, D(2026, 3, 2), order=DiscogsOrder(order_id="2"))
    _lot(30.0, D(2025, 12, 1))
    po = PurchaseOrder(date=D(2026, 1, 10), price=25.0)
    db.session.add(po)
    db.session.commit()
    db.session.add_all(
        [
            Expense(date=D(2026, 2, 1), category="fees", amount=5.0),
            Expense(
                date=D(2026, 1, 10), category="supplies", amount=5.0, purchase_order=po
            ),
        ]
    )
    db.session.commit()

    r = reports.profit_and_loss(
        reports.Period({"from": "2026-01-01", "to": "2026-01-31"})
    )
    t = r["total"]
    assert (t["receipts"], t["gross"], t["net"], t["fees"]) == (1, 40.0, 34.0, 6.0)
    assert t["cogs"] == 50.0  # half of the $100 Lot
    assert t["lots"] == 100.0
    assert t["supplies"] == 20.0  # the order, less its $5 Expense
    assert t["expenses"] == 5.0
    assert t["cogs_profit"] == 34.0 - 50.0 - 20.0 - 5.0
    assert t["cash_profit"] == 34.0 - 100.0 - 20.0 - 5.0
    assert r["marketplaces"]["eBay"]["net"] == 34.0
    assert r["marketplaces"]["Discogs"]["receipts"] == 0
    assert [c[0] for c in r["columns"]] == ["2026-01"]

    year = reports.profit_and_loss(
        reports.Period({"from": "2026-01-01", "to": "2026-12-31"})
    )
    assert year["total"]["receipts"] == 2
    assert year["marketplaces"]["Discogs"]["net"] == 50.0
    assert year["expenses"] == {
        "Commissions and fees (line 10)": 5.0,
        "Supplies (line 22)": 5.0,
    }
    assert len(year["columns"]) == 12


def test_all_time_cash_profit_agrees_with_the_home_page(app):
    lot = _lot(100.0, D(2024, 1, 5))
    lot2 = _lot(40.0, D(2025, 6, 1))
    _sale(lot, 40.0, 34.0, D(2024, 2, 1), order=EbayOrder(order_id="1"))
    _sale(lot, 60.0, 50.0, D(2025, 3, 2), order=DiscogsOrder(order_id="2"))
    _sale(lot2, 10.0, 9.0, D(2025, 7, 2))
    # A Unit still in stock: its Lot's cost counts on the cash basis.
    db.session.add(
        Unit(
            name="S",
            unit_type='12" LP',
            pressing="",
            grading="",
            purchase_lot=lot2,
            sold=False,
        )
    )
    po = PurchaseOrder(date=D(2025, 1, 10), price=25.0)
    db.session.add(po)
    db.session.commit()
    db.session.add_all(
        [
            Expense(date=D(2025, 2, 1), category="fees", amount=5.0),
            Expense(
                date=D(2025, 1, 10), category="supplies", amount=5.0, purchase_order=po
            ),
        ]
    )
    db.session.commit()

    home = index.home_totals()
    r = reports.profit_and_loss(reports.Period({"period": "all"}))["total"]
    assert r["net"] == pytest.approx(home["totals"]["net"])
    assert r["gross"] == pytest.approx(home["totals"]["gross"])
    assert r["fees"] == pytest.approx(home["totals"]["fees"])
    assert r["lots"] == pytest.approx(home["totals"]["capital"])
    assert r["supplies"] == pytest.approx(home["supply_total"])
    assert r["expenses"] == pytest.approx(home["expense_total"])
    assert r["cash_profit"] == pytest.approx(home["total_net_profit"])
    by_marketplace = {m["name"]: m for m in home["marketplace_sales"]}
    mp = reports.profit_and_loss(reports.Period({"period": "all"}))["marketplaces"]
    assert mp["eBay"]["net"] == by_marketplace["eBay"]["net"]
    assert mp["Discogs"]["net"] == by_marketplace["Discogs"]["net"]
    assert mp[""]["net"] == by_marketplace["Other"]["net"]
    # Cost of goods leaves out Units not sold.
    assert r["cogs"] == pytest.approx(50.0 + 50.0 + 20.0)


def test_other_items_stay_out_of_record_figures(app):
    receipt = SalesReceipt(date=D(2026, 2, 1), sold_price=20.0, net_sold=17.0)
    costed = SalesReceipt(date=D(2026, 2, 2), sold_price=30.0, net_sold=25.0)
    db.session.add_all([receipt, costed])
    db.session.commit()
    db.session.add_all(
        [
            OtherItem(name="Shirt", sold=True, sales_receipt=receipt),
            OtherItem(name="Mug", sold=True, sales_receipt=costed, cost=5.0),
        ]
    )
    db.session.commit()
    r = reports.profit_and_loss(reports.Period({"period": "all"}))
    assert r["total"]["receipts"] == 0 and r["total"]["net"] == 0
    assert r["other"]["items"] == 2 and r["other"]["net"] == 42.0
    # Only the Mug has a cost, so only it has a profit.
    assert r["other"]["profit"] == 20.0
    empty = reports.profit_and_loss(
        reports.Period({"from": "2020-01-01", "to": "2020-02-01"})
    )
    assert empty["other"]["profit"] is None


def test_the_page_renders(app):
    lot = _lot(100.0, D(2026, 1, 5))
    _sale(lot, 40.0, 34.0, D(2026, 1, 20), order=EbayOrder(order_id="1"))
    with signed_in_client(app) as client:
        for query in (
            "",
            "?period=all&group=quarter",
            "?from=junk",
            "?from=2026-01-01&to=2026-12-31",
        ):
            response = client.get("/profitandloss/" + query)
            assert response.status_code == 200, query
        assert b"Profit" in response.data
        assert b"$34.00" in response.data


def test_the_number_of_queries_does_not_grow(app):
    from sqlalchemy import event

    def count_queries():
        n = []
        engine = db.session.get_bind()
        event.listen(engine, "before_cursor_execute", lambda *a: n.append(1))
        reports.profit_and_loss(reports.Period({"period": "all", "group": "month"}))
        return len(n)

    lot = _lot(100.0, D(2024, 1, 5))
    _sale(lot, 40.0, 34.0, D(2024, 2, 1), order=EbayOrder(order_id="1"))
    few = count_queries()
    for i in range(5):
        _sale(lot, 40.0, 34.0, D(2025, 2, 1 + i), order=EbayOrder(order_id=str(10 + i)))
    assert count_queries() == few


def test_a_receipt_with_no_units_still_counts(app):
    # The home page's Sales by marketplace counts it, though its Net profit
    # (which adds up Units) doesn't; the report follows the first.
    db.session.add(SalesReceipt(date=D(2026, 1, 2), sold_price=5.0, net_sold=3.31))
    db.session.commit()
    total = reports.profit_and_loss(reports.Period({"period": "all"}))["total"]
    assert total["net"] == 3.31 and total["receipts"] == 1


def _unit(lot, sold=False, receipt=None, retail=None):
    unit = Unit(
        name="U",
        unit_type='12" LP',
        pressing="",
        grading="",
        purchase_lot=lot,
        sold=sold,
        sales_receipt=receipt,
        retail_price=retail,
    )
    db.session.add(unit)
    db.session.commit()
    return unit


def _inventory(**period):
    return reports.inventory(reports.Period(period))


def test_inventory_adds_up_and_agrees_with_profit_and_loss(app):
    jan = _lot(100.0, D(2026, 1, 5))  # 4 Units, $25 each
    feb = _lot(30.0, D(2026, 2, 5))  # 2 Units, $15 each
    _sale(jan, 40.0, 34.0, D(2026, 2, 10), order=EbayOrder(order_id="1"))
    _sale(jan, 20.0, 17.0, D(2026, 3, 1), order=EbayOrder(order_id="2"))
    _sale(feb, 50.0, 40.0, D(2026, 3, 2), order=EbayOrder(order_id="3"))
    _unit(jan, retail=12.0)
    _unit(jan, retail=8.0)
    _unit(feb)
    # 6 Units were bought, 3 sold.
    got = _inventory(**{"from": "2026-02-01", "to": "2026-03-31"})
    t = got["total"]
    assert t["opening"]["units"] == 4 and t["opening"]["cost"] == 100.0
    assert t["bought"]["units"] == 2 and t["bought"]["cost"] == 30.0
    assert t["sold"]["units"] == 3 and t["sold"]["cost"] == 65.0
    assert t["closing"]["units"] == 3
    assert t["closing"]["cost"] == 65.0
    assert t["closing"]["retail"] == 20.0
    for f in [t, *[c for _, c in got["columns"]]]:
        assert (
            f["opening"]["units"] + f["bought"]["units"] - f["sold"]["units"]
            == (f["closing"]["units"])
        )
        assert round(
            f["opening"]["cost"] + f["bought"]["cost"] - f["sold"]["cost"], 2
        ) == round(f["closing"]["cost"], 2)
    # Each column opens where the one before closed.
    cols = [c for _, c in got["columns"]]
    assert cols[1]["opening"] == cols[0]["closing"]
    # Cost of goods sold is the Profit & Loss figure.
    pl = reports.profit_and_loss(
        reports.Period({"from": "2026-02-01", "to": "2026-03-31"})
    )
    assert pl["total"]["cogs"] == t["sold"]["cost"]


def test_inventory_all_time_starts_empty_and_closing_is_the_home_page(app):
    lot = _lot(60.0, D(2025, 6, 1))
    _sale(lot, 10.0, 8.0, D(2025, 7, 1), order=EbayOrder(order_id="1"))
    _unit(lot)
    _unit(lot)
    _unit(None)  # no Lot: in stock from the beginning, at no cost
    got = _inventory(period="all")
    assert got["total"]["opening"]["units"] == 1
    assert got["total"]["closing"]["units"] == index.home_totals()["stock_total"] == 3
    assert got["total"]["closing"]["cost"] == 40.0
    assert got["flags"]["no_lot"] == 1


def test_inventory_leaves_out_odd_units_and_counts_them(app):
    lot = _lot(30.0, D(2026, 1, 5))
    _unit(lot, sold=True)  # sold, but no Sales Receipt
    _unit(lot)
    # Sold before the Lot's date.
    _sale(lot, 5.0, 4.0, D(2026, 1, 1), order=EbayOrder(order_id="1"))
    got = _inventory(period="all")
    assert got["flags"] == {"unset": 0, "no_receipt": 1, "no_lot": 0, "sold_first": 1}
    assert got["total"]["closing"]["units"] == index.home_totals()["stock_total"] == 1


def test_other_items_stay_out_of_inventory(app):
    lot = _lot(10.0, D(2026, 1, 5))
    _unit(lot)
    receipt = SalesReceipt(date=D(2026, 1, 6), sold_price=5.0, net_sold=4.0)
    db.session.add(receipt)
    db.session.add(OtherItem(name="Mug", sold=True, sales_receipt=receipt))
    db.session.commit()
    got = _inventory(period="all")
    assert got["total"]["closing"]["units"] == 1
    assert got["total"]["sold"]["units"] == 0


def test_lots_report_lists_lots_bought_in_the_period(app):
    old = _lot(50.0, D(2025, 12, 31))
    new = _lot(40.0, D(2026, 1, 5))  # 2 Units
    _sale(new, 30.0, 25.0, D(2026, 2, 1), order=EbayOrder(order_id="1"))
    _unit(new, retail=9.0)
    _sale(old, 10.0, 8.0, D(2026, 1, 9), order=EbayOrder(order_id="2"))
    got = reports.lots_report(
        reports.Period({"from": "2026-01-01", "to": "2026-12-31"})
    )
    assert [r["lot"].id for r in got["rows"]] == [new.id]
    row = got["rows"][0]
    assert (row["units"], row["sold_units"], row["instock"]) == (2, 1, 1)
    assert row["stock_cost"] == 20.0
    assert (row["net"], row["profit"]) == (25.0, -15.0)
    assert got["total"]["capital"] == 40.0 and got["total"]["stock_cost"] == 20.0
    # The same figures as the Purchase Lots list.
    assert row["profit"] == reports.finance.lot_figures()[new.id]["profit"]


def test_inventory_and_lots_pages_render(app):
    lot = _lot(100.0, D(2026, 1, 5))
    _sale(lot, 40.0, 34.0, D(2026, 1, 20), order=EbayOrder(order_id="1"))
    with signed_in_client(app) as client:
        for path in ("/inventoryreport/", "/lotsreport/"):
            for query in ("", "?period=all&group=quarter", "?from=junk"):
                assert client.get(path + query).status_code == 200, path + query
        assert b"Closing stock" in client.get("/inventoryreport/?period=all").data
        assert b"$100.00" in client.get("/lotsreport/?period=all").data
    empty = reports.Period({"from": "2020-01-01", "to": "2020-01-31"})
    assert reports.lots_report(empty)["rows"] == []
