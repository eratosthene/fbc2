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
