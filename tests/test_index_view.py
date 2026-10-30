import datetime

import pytest

from fbc import db
from fbc.index import MyIndexView
from fbc.models.inventory import PurchaseLot, StorageBox, Unit
from fbc.models.sales import SalesReceipt
from fbc.models.supplies import PurchaseOrder


def _lot(name="Lot 1", price=100.0):
    lot = PurchaseLot(name=name, date=datetime.date(2024, 1, 1), price=price)
    db.session.add(lot)
    db.session.commit()
    return lot


def _box(name="Box 1"):
    box = StorageBox(name=name)
    db.session.add(box)
    db.session.commit()
    return box


def _receipt(sold_price, net_sold):
    receipt = SalesReceipt(
        date=datetime.date(2024, 2, 1), sold_price=sold_price, net_sold=net_sold
    )
    db.session.add(receipt)
    db.session.commit()
    return receipt


def _unit(lot, box=None, sold=False, sales_receipt=None, name="Unit"):
    unit = Unit(
        name=name,
        unit_type='12" LP',
        pressing="",
        grading="",
        purchase_lot=lot,
        storage_box=box,
        sold=sold,
        sales_receipt=sales_receipt,
    )
    db.session.add(unit)
    db.session.commit()
    return unit


def test_index_renders_successfully_with_data(app):
    lot = _lot()
    _unit(lot, sold=False, name="Unsold")
    _unit(lot, sold=True, name="Sold", sales_receipt=_receipt(50.0, 45.0))

    with app.test_client() as client:
        response = client.get("/")
        assert response.status_code == 200


def test_index_counts_stock_and_unit_totals(app):
    lot = _lot()
    _unit(lot, sold=False, name="Unsold")
    _unit(lot, sold=True, name="Sold", sales_receipt=_receipt(50.0, 45.0))

    captured = {}

    class CapturingIndexView(MyIndexView):
        def render_template(self, template, **kwargs):
            captured.update(kwargs)
            return "ok"

    with app.test_request_context("/"):
        view = CapturingIndexView()
        view.appbuilder = app.extensions["appbuilder"]
        view.index()

    assert captured["stock_total"] == 1
    assert captured["unit_total"] == 2


def test_index_aggregates_totals_across_lots_and_boxes(app):
    lot = _lot(name="Lot A", price=100.0)
    box = _box(name="Box A")
    receipt = _receipt(sold_price=60.0, net_sold=54.0)
    _unit(lot, box=box, sold=True, sales_receipt=receipt, name="Sold Unit")
    _unit(lot, box=box, sold=False, name="Unsold Unit")

    po = PurchaseOrder(date=datetime.date(2024, 1, 1), price=10.0, notes="tape")
    db.session.add(po)
    db.session.commit()

    captured = {}

    class CapturingIndexView(MyIndexView):
        def render_template(self, template, **kwargs):
            captured.update(kwargs)
            return "ok"

    with app.test_request_context("/"):
        view = CapturingIndexView()
        view.appbuilder = app.extensions["appbuilder"]
        view.index()

    assert captured["stock_total"] == 1
    assert captured["unit_total"] == 2
    assert captured["supply_total"] == 10.0
    assert captured["totals"]["gross"] == 60.0
    assert captured["totals"]["net"] == 54.0
    assert captured["totals"]["fees"] == 6.0
    assert captured["totals"]["profit"] == -46.0
    assert captured["total_net_profit"] == -56.0
    assert captured["profit_margin"] == pytest.approx(-56.0 / 54.0 * 100)
    assert captured["supply_per_unit"] == 5.0
    # (100.0 capital / 2 units) / (1 - 10% fees) + 5.0 supplies per unit
    assert captured["breakeven_per_unit"] == pytest.approx(50.0 / 0.9 + 5.0)

    box_totals = captured["box_totals"]
    assert box_totals[box.id]["instock"] == 1
    assert box_totals[box.id]["gross"] == 60.0
    assert box_totals[box.id]["net"] == 54.0
    # capital comes from the lot's per-unit price (100.0 / 2 units = 50.0),
    # added once for each of the box's units.
    assert box_totals[box.id]["capital"] == 100.0

    lot_totals = captured["lot_totals"]
    assert lot_totals[lot.id]["gross"] == 60.0
    assert lot_totals[lot.id]["net"] == 54.0
    assert lot_totals[lot.id]["instock"] == 1


def test_index_handles_units_without_storage_box(app):
    lot = _lot(name="Lot B", price=50.0)
    receipt = _receipt(sold_price=30.0, net_sold=27.0)
    _unit(lot, box=None, sold=True, sales_receipt=receipt, name="Sold, No Box")

    captured = {}

    class CapturingIndexView(MyIndexView):
        def render_template(self, template, **kwargs):
            captured.update(kwargs)
            return "ok"

    with app.test_request_context("/"):
        view = CapturingIndexView()
        view.appbuilder = app.extensions["appbuilder"]
        view.index()

    assert captured["box_totals"][0]["name"] == "No Box Assigned"
    assert captured["box_totals"][0]["gross"] == 30.0
    assert captured["box_totals"][0]["net"] == 27.0


def test_index_renders_with_empty_inventory(app):
    with app.test_client() as client:
        response = client.get("/")
        assert response.status_code == 200


def test_index_renders_with_no_sales(app):
    lot = _lot()
    _unit(lot, sold=False, name="Unsold")

    with app.test_client() as client:
        response = client.get("/")
        assert response.status_code == 200


@pytest.mark.parametrize(
    "path", ["/syncebaylistings", "/syncebayorders", "/syncebayordersdeep"]
)
def test_ebay_sync_without_connect_ebay_says_what_to_do(app, monkeypatch, path):
    from fbc import listing

    monkeypatch.setitem(app.config["EBAY_SETTINGS"], "OAUTH_REFRESH_TOKEN", "")
    monkeypatch.setattr(listing, "_oauth", {})
    client = app.test_client()

    response = client.get(path)

    assert response.status_code == 302
    with client.session_transaction() as session:
        messages = [m for _, m in session.get("_flashes", [])]
    assert any("Connect eBay" in m for m in messages)
