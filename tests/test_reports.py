import datetime

import pytest
from auth import signed_in_client

from fbc import db
from fbc.models.expenses import Expense
from fbc.models.discogs import DiscogsOrder
from fbc.models.ebay import EbayOrder
from fbc.models.inventory import PurchaseLot, Unit
from fbc.models.other import OtherItem
from fbc.models.sales import SalesReceipt, SalesReceiptLine
from fbc.models.supplies import PurchaseOrder, PurchaseOrderSupply, Supply, SupplyUse
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


def test_sales_averages_and_days_to_sell(app):
    lot = _lot(75.0, D(2026, 1, 1))  # 3 Units, $25 each
    _sale(lot, 40.0, 34.0, D(2026, 1, 11), order=EbayOrder(order_id="1"))  # 10 days
    _sale(lot, 20.0, 15.0, D(2026, 1, 31), order=EbayOrder(order_id="2"))  # 30 days
    _sale(lot, 10.0, 8.0, D(2026, 3, 2))  # outside the period
    got = reports.sales_report(
        reports.Period({"from": "2026-01-01", "to": "2026-02-28"})
    )
    t = got["total"]
    assert t["receipts"] == 2 and t["units"] == 2
    assert t["gross"] == 60.0 and t["net"] == 49.0 and t["fees"] == 11.0
    assert t["avg_receipt"] == 30.0 and t["avg_unit"] == 30.0
    assert t["avg_net_unit"] == 24.5
    assert t["avg_profit_unit"] == -0.5  # 49 - 2 * 25, over 2 Units
    assert t["days"]["median"] == 20 and t["days"]["fastest"] == 10
    assert t["days"]["slowest"] == 30 and t["days"]["q1"] == 15
    assert t["days_unknown"] == 0
    # The columns add up to the total.
    assert [c["receipts"] for _, c in got["columns"]] == [2, 0]
    # It agrees with Profit & Loss.
    pl = reports.profit_and_loss(
        reports.Period({"from": "2026-01-01", "to": "2026-02-28"})
    )["total"]
    assert (pl["gross"], pl["net"], pl["fees"]) == (t["gross"], t["net"], t["fees"])


def test_sales_split_a_receipt_between_its_units(app):
    lot = _lot(20.0, D(2026, 1, 1))  # 2 Units, $10 each
    receipt = _sale(lot, 30.0, 24.0, D(2026, 1, 5))
    _unit(lot, sold=True, receipt=receipt)
    t = reports.sales_report(reports.Period({"period": "all"}))["total"]
    assert t["receipts"] == 1 and t["units"] == 2
    assert t["avg_receipt"] == 30.0 and t["avg_unit"] == 15.0
    assert t["avg_profit_unit"] == 2.0  # 12 net each, less 10


def test_sales_leave_out_days_that_cant_be_counted(app):
    lot = _lot(10.0, D(2026, 1, 10))
    _sale(lot, 5.0, 4.0, D(2026, 1, 1))  # sold before its Lot
    _sale(None, 5.0, 4.0, D(2026, 1, 2))  # no Lot
    t = reports.sales_report(reports.Period({"period": "all"}))["total"]
    assert t["units"] == 2 and t["days"] is None and t["days_unknown"] == 2


def test_sales_gross_by_year(app):
    lot = _lot(50.0, D(2025, 1, 1))
    ebay = _sale(lot, 40.0, 34.0, D(2025, 12, 31), order=EbayOrder(order_id="1"))
    db.session.add(
        SalesReceiptLine(sales_receipt=ebay, kind="SALE", label="Sale", amount=46.0)
    )
    _sale(lot, 20.0, 15.0, D(2026, 1, 1), order=DiscogsOrder(order_id="2"))
    # Other Items never count.
    receipt = SalesReceipt(date=D(2026, 1, 2), sold_price=99.0, net_sold=90.0)
    db.session.add(receipt)
    db.session.add(OtherItem(name="Mug", sold=True, sales_receipt=receipt))
    db.session.commit()
    years = dict(
        reports.sales_report(
            reports.Period({"from": "2026-01-01", "to": "2026-01-31"})
        )["years"]
    )
    assert sorted(years) == [2025, 2026]
    assert years[2025]["eBay"] == {"gross": 40.0, "sale_lines": 46.0}
    # No lines: the sold for stands in.
    assert years[2026]["Discogs"] == {"gross": 20.0, "sale_lines": 20.0}
    assert years[2026]["total"]["gross"] == 20.0


def test_aging_groups_stock_by_lot_age_and_agrees_with_inventory(app):
    old = _lot(40.0, D(2025, 1, 15))  # 2 Units, $20 each
    mid = _lot(30.0, D(2025, 9, 1))  # 3 Units, $10 each
    new = _lot(10.0, D(2026, 3, 1))  # 1 Unit
    _unit(old, retail=5.0)
    _unit(old, retail=7.0)
    _unit(mid)
    _unit(mid)
    _unit(
        mid,
        sold=True,
        receipt=SalesReceipt(date=D(2026, 1, 1), sold_price=1.0, net_sold=1.0),
    )
    _unit(new)
    _unit(None)
    period = {"from": "2026-01-01", "to": "2026-03-31"}
    got = reports.aging(reports.Period(period))
    assert got["as_of"] == D(2026, 3, 31)
    buckets = dict(got["buckets"])
    assert buckets["0–3 months"]["units"] == 1
    assert buckets["3–6 months"]["units"] == 0
    assert buckets["6–12 months"]["units"] == 2  # 1 Sep to 31 Mar is 6 months
    assert buckets["A year or more"] == {"units": 2, "cost": 40.0, "retail": 12.0}
    assert got["no_lot"]["units"] == 1
    # The Units are Inventory's closing stock.
    closing = _inventory(**period)["total"]["closing"]
    assert got["total"]["units"] == closing["units"] == 6
    assert got["total"]["cost"] == closing["cost"]
    # The old Lots, most cost first.
    assert [r["lot"].id for r in got["old_lots"]] == [old.id, mid.id]
    assert got["old_lots"][1]["units"] == 2
    # A different cut-off: only the Lot over a year old.
    twelve = reports.aging(reports.Period(period), 12)
    assert [r["lot"].id for r in twelve["old_lots"]] == [old.id]
    assert reports.old_after_months("9") == 9
    for bad in (None, "x", "0", "121", "-3"):
        assert reports.old_after_months(bad) == reports.OLD_AFTER_MONTHS


def test_aging_with_no_end_is_as_of_today(app):
    lot = _lot(10.0, D(2020, 1, 1))
    _unit(lot)
    got = reports.aging(reports.Period({"period": "all"}))
    assert got["as_of"] == reports.today()
    assert dict(got["buckets"])["A year or more"]["units"] == 1
    assert got["total"]["units"] == index.home_totals()["stock_total"] == 1


def test_sales_and_aging_pages_render(app):
    lot = _lot(100.0, D(2026, 1, 5))
    _sale(lot, 40.0, 34.0, D(2026, 1, 20), order=EbayOrder(order_id="1"))
    _unit(lot)
    with signed_in_client(app) as client:
        for path in ("/salesreport/", "/agingreport/"):
            for query in ("", "?period=all&group=quarter", "?from=junk"):
                assert client.get(path + query).status_code == 200, path + query
        assert b"1099-K" in client.get("/salesreport/?period=all").data
        assert b"$40.00" in client.get("/salesreport/?period=all").data
        assert b"A year or more" in client.get("/agingreport/?period=all").data
        page = client.get("/agingreport/?period=all&old=9").data
        assert b'name="old" value="9"' in page
        assert b'name="old" value="6"' in client.get("/agingreport/?old=junk").data


def _supplies_fixture():
    """A Lot of two Units sold in January and March, with supplies and uses."""
    lot = _lot(100.0, D(2026, 1, 5))
    jan = _sale(lot, 40.0, 34.0, D(2026, 1, 20), order=EbayOrder(order_id="1"))
    mar = _sale(lot, 60.0, 50.0, D(2026, 3, 2), order=DiscogsOrder(order_id="2"))
    po = PurchaseOrder(date=D(2026, 1, 10), price=25.0)
    mailer = Supply(name="Mailer", quantity=50, cost_per_use=0.5)
    tape = Supply(name="Tape", quantity=5)
    db.session.add_all([po, mailer, tape])
    db.session.commit()
    db.session.add_all(
        [
            Expense(
                date=D(2026, 1, 10), category="supplies", amount=5.0, purchase_order=po
            ),
            PurchaseOrderSupply(purchase_order=po, supply=mailer, quantity=50),
            SupplyUse(
                date=datetime.datetime(2026, 1, 20, 18),
                supply=mailer,
                count=2,
                kind="shipping",
                sales_receipt=jan,
            ),
            SupplyUse(
                date=datetime.datetime(2026, 3, 2, 18),
                supply=tape,
                count=1,
                kind="shipping",
                sales_receipt=mar,
            ),
            # A restock isn't a use.
            SupplyUse(
                date=datetime.datetime(2026, 1, 10, 18),
                supply=mailer,
                count=-50,
                kind="restock",
            ),
        ]
    )
    db.session.commit()
    return lot


def test_supplies_agree_with_profit_and_loss(app):
    _supplies_fixture()
    period = reports.Period({"from": "2026-01-01", "to": "2026-03-31"})
    s = reports.supplies_report(period)
    t = s["total"]
    assert t["bought"] == 20.0  # the order, less its $5 Expense
    assert t["net"] == reports.profit_and_loss(period)["total"]["supplies"]
    assert (t["units_sold"], t["per_unit"]) == (2, 10.0)
    assert (t["used"], t["used_cost"], t["uncosted"]) == (3, 1.0, 1)
    assert t["used_per_unit"] == 0.5
    jan = dict(s["columns"])["2026-01"]
    assert (jan["bought"], jan["used"], jan["used_cost"]) == (20.0, 2, 1.0)
    rows = {r["supply"].name: r for r in s["per_supply"]}
    assert (rows["Mailer"]["ordered"], rows["Mailer"]["used"]) == (50, 2)
    assert rows["Mailer"]["used_cost"] == 1.0
    assert rows["Tape"]["used_cost"] is None and rows["Tape"]["cost_per_use"] is None
    # Outside the period there is nothing.
    empty = reports.supplies_report(
        reports.Period({"from": "2020-01-01", "to": "2020-01-31"})
    )
    assert empty["per_supply"] == [] and empty["total"]["per_unit"] is None


def test_supplies_used_on_other_items_stay_out_of_use(app):
    receipt = SalesReceipt(date=D(2026, 2, 1), sold_price=20.0, net_sold=17.0)
    mailer = Supply(name="Mailer", quantity=5, cost_per_use=2.0)
    db.session.add_all([receipt, mailer])
    db.session.commit()
    db.session.add_all(
        [
            OtherItem(name="Shirt", sold=True, sales_receipt=receipt),
            SupplyUse(
                date=datetime.datetime(2026, 2, 1, 18),
                supply=mailer,
                count=1,
                kind="shipping",
                sales_receipt=receipt,
            ),
        ]
    )
    db.session.commit()
    period = reports.Period({"period": "all"})
    t = reports.supplies_report(period)["total"]
    assert t["used"] == 0
    assert t["other_used"] == 2.0 and t["net"] == -2.0
    assert t["net"] == reports.profit_and_loss(period)["total"]["supplies"]


def test_break_even(app):
    _supplies_fixture()
    period = reports.Period({"from": "2026-01-01", "to": "2026-01-31"})
    b = reports.breakeven(period)
    # All time: $50 a Unit, 16% fees, $10 of supplies a Unit.
    assert b["all"]["units"] == 2
    assert b["all"]["breakeven"] == pytest.approx(50 / 0.84 + 10, abs=0.01)
    assert b["all"]["breakeven"] == pytest.approx(
        index.home_totals()["breakeven_per_unit"], abs=0.01
    )
    # January: one Unit sold, costing $50, fees 15%, the $20 of supplies.
    assert b["period"]["units"] == 1
    assert b["period"]["breakeven"] == pytest.approx(50 / 0.85 + 20, abs=0.01)
    # Spent 100 + 20 + the $5 Expense, against 84 of net sales: $41 over, at $34
    # net a Unit (January's).
    s = b["still_to_sell"]
    assert (s["outstanding"], s["average"], s["average_of"]) == (
        41.0,
        34.0,
        "the period",
    )
    assert s["units"] == 2
    empty = reports.breakeven(
        reports.Period({"from": "2020-01-01", "to": "2020-01-31"})
    )
    assert empty["period"]["breakeven"] is None
    assert empty["still_to_sell"]["average_of"] == "all time"


def test_break_even_with_nothing(app):
    b = reports.breakeven(reports.Period({"period": "all"}))
    assert b["all"]["breakeven"] is None
    assert b["still_to_sell"] == {
        "outstanding": 0.0,
        "average": 0.0,
        "average_of": "",
        "units": 0,
    }


def test_lots_say_how_many_units_to_break_even(app):
    lot = _supplies_fixture()
    other = _lot(500.0, D(2026, 2, 1))
    rows = {
        r["lot"].id: r
        for r in reports.lots_report(reports.Period({"period": "all"}))["rows"]
    }
    # $100 Lot, $84 back; the average net a Unit over all time is $42.
    assert rows[lot.id]["to_break_even"] == 1
    assert rows[other.id]["to_break_even"] == 12


def test_supplies_query_count_does_not_grow(app):
    from sqlalchemy import event

    def count_queries():
        n = []
        engine = db.session.get_bind()
        event.listen(engine, "before_cursor_execute", lambda *a: n.append(1))
        reports.supplies_report(reports.Period({"period": "all"}))
        return len(n)

    lot = _supplies_fixture()
    few = count_queries()
    mailer = db.session.query(Supply).filter_by(name="Mailer").one()
    for i in range(5):
        receipt = _sale(lot, 40.0, 34.0, D(2026, 4, 1 + i))
        db.session.add(
            SupplyUse(
                date=datetime.datetime(2026, 4, 1 + i, 18),
                supply=mailer,
                count=1,
                kind="shipping",
                sales_receipt=receipt,
            )
        )
    db.session.commit()
    assert count_queries() == few


def test_csv_downloads(app):
    from fbc.sales import report_csv

    _supplies_fixture()
    db.session.add(PurchaseLot(name="=HYPERLINK()", date=D(2026, 2, 1), price=7.0))
    db.session.commit()
    with signed_in_client(app) as client:
        for path in (
            "/profitandloss",
            "/inventoryreport",
            "/lotsreport",
            "/salesreport",
            "/agingreport",
            "/suppliesreport",
        ):
            for query in ("", "?period=all&group=quarter", "?from=junk"):
                response = client.get(f"{path}/download{query}")
                assert response.status_code == 200, path + query
                assert response.mimetype == "text/csv"
                assert "attachment" in response.headers["Content-Disposition"]
        text = client.get("/profitandloss/download?period=all").get_data(as_text=True)
        assert text.splitlines()[0].startswith(",Total,2026-01")
        assert "Net sold,84.00," in text
        lots = client.get("/lotsreport/download?period=all").get_data(as_text=True)
        assert "'=HYPERLINK()" in lots
        records = client.get("/profitandloss/records?period=all").get_data(as_text=True)
        lines = records.splitlines()
        assert lines[0] == "Type,Date,Description,Amount,Net"
        assert "Sales Receipt,2026-01-20,eBay,40.00,34.00" in lines
        assert "Purchase Lot,2026-01-05,Lot,100.00," in lines
        assert "Supplies,2026-01-10,,20.00," in lines
        assert "Expense,2026-01-10,Supplies (line 22),5.00," in lines
        assert "Download CSV" in client.get("/suppliesreport/").get_data(as_text=True)
    assert report_csv.render([["a,b", 1.5, None]]) == '"a,b",1.50,\r\n'


def test_supplies_page_renders(app):
    _supplies_fixture()
    with signed_in_client(app) as client:
        for query in ("", "?period=all&group=quarter", "?from=junk"):
            assert client.get("/suppliesreport/" + query).status_code == 200, query
        page = client.get("/suppliesreport/?period=all").get_data(as_text=True)
        assert "Break-even per Unit" in page and "Mailer" in page
        assert "Break-even" in client.get("/profitandloss/?period=all").get_data(
            as_text=True
        )


def test_profit_and_loss_chart_data(app):
    lot = _lot(100.0, D(2026, 1, 5))
    _sale(lot, 40.0, 34.0, D(2026, 2, 20), order=EbayOrder(order_id="1"))
    _sale(lot, 60.0, 50.0, D(2026, 3, 2), order=EbayOrder(order_id="2"))
    _lot(30.0, D(2025, 12, 1))
    c = reports.profit_and_loss(
        reports.Period({"from": "2026-01-01", "to": "2026-03-31"})
    )["chart"]
    money = c["money"]
    assert money["labels"] == ["2026-01", "2026-02", "2026-03"]
    net, spending, profit = (s["data"] for s in money["series"])
    assert net == [0.0, 34.0, 50.0]
    assert spending == [100.0, 0.0, 0.0]
    assert profit == [-100.0, 34.0, 50.0]
    # The December Lot is before the period: it starts the cumulative line.
    cumulative = c["cumulative"]["series"][0]["data"]
    assert cumulative == [-130.0, -96.0, -46.0]
    assert c["cumulative"]["series"][1]["kind"] == "baseline"
    # The whole run, from nothing, ends where all-time cash profit does.
    everything = reports.profit_and_loss(reports.Period({"period": "all"}))
    assert (
        everything["chart"]["cumulative"]["series"][0]["data"][-1]
        == (everything["total"]["cash_profit"])
    )


def test_inventory_chart_data(app):
    lot = _lot(100.0, D(2026, 1, 5))
    _sale(lot, 40.0, 34.0, D(2026, 2, 20))
    _unit(lot)
    c = reports.inventory(reports.Period({"from": "2026-01-01", "to": "2026-02-28"}))[
        "chart"
    ]
    assert c["value"]["series"][0]["data"] == [100.0, 50.0]
    assert c["sold"]["series"][0]["data"] == [0, 1]


def test_lot_payback_chart(app):
    big = _lot(200.0, D(2026, 1, 5))
    _sale(big, 100.0, 90.0, D(2026, 1, 20))
    _sale(big, 100.0, 90.0, D(2026, 2, 10))
    small = _lot(50.0, D(2026, 2, 3))
    _sale(small, 60.0, 55.0, D(2026, 2, 5))
    for i in range(6):
        _lot(1.0 + i, D(2026, 1, 1))
    p = reports.lots_report(reports.Period({"from": "2026-01-01", "to": "2026-03-31"}))[
        "payback"
    ]
    # The five dearest, and the Paid back line.
    assert len(p["series"]) == 6
    first = p["series"][0]
    assert first["label"].startswith("Lot (2026-01-05)")
    assert first["data"][:3] == [45.0, 90.0, 90.0]
    assert p["series"][-1] == {
        "label": "Paid back",
        "kind": "baseline",
        "data": [100] * len(p["labels"]),
    }
    assert (
        reports.lots_report(reports.Period({"from": "2020-01-01", "to": "2020-01-31"}))[
            "payback"
        ]
        is None
    )


def test_year_to_date_for_the_home_page(app):
    lot = _lot(100.0, D(2026, 1, 5))
    _sale(lot, 40.0, 34.0, D(2026, 2, 20), order=EbayOrder(order_id="1"))
    _sale(lot, 60.0, 50.0, D(2025, 3, 2), order=EbayOrder(order_id="2"))
    y = reports.year_to_date(now=D(2026, 6, 1))
    assert (y["start"], y["end"]) == (D(2026, 1, 1), D(2026, 6, 1))
    assert (y["net"], y["receipts"], y["units_sold"]) == (34.0, 1, 1)
    assert y["profit"] == 34.0 - 50.0
    assert y["margin"] == pytest.approx((34.0 - 50.0) / 34.0 * 100, abs=0.01)
    # The same as Profit & Loss's year to date.
    pnl = reports.profit_and_loss(reports.Period({"period": "ytd"}))["total"]
    now = reports.year_to_date()
    assert now["net"] == pnl["net"] and now["profit"] == pnl["cogs_profit"]


def test_chart_pages_and_home_summary_render(app):
    lot = _lot(100.0, reports.today().replace(day=1))
    _sale(lot, 40.0, 34.0, reports.today(), order=EbayOrder(order_id="1"))
    with signed_in_client(app) as client:
        for path in ("/profitandloss/", "/inventoryreport/", "/lotsreport/"):
            page = client.get(path + "?period=all").get_data(as_text=True)
            assert "js/chart.umd.js" in page and "report-charts.js" in page, path
            assert 'class="report-chart"' in page, path
        assert b"Cumulative cash position" in client.get("/profitandloss/").data
        assert client.get("/static/js/chart.umd.js").status_code == 200
        assert client.get("/static/js/report-charts.js").status_code == 200
        home = client.get("/").get_data(as_text=True)
        assert "Year to date" in home and "$34.00" in home
        assert "/profitandloss/" in home and "/suppliesreport/" in home
