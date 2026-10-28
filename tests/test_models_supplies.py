import datetime

from fbc.models.supplies import PurchaseOrder, Supply


def test_purchase_order_purchase_price_formats_currency():
    po = PurchaseOrder(date=datetime.date(2024, 1, 1), price=12.5, notes="boxes")
    assert po.purchase_price() == "$12.50"


def test_purchase_order_repr_includes_date_notes_and_price():
    po = PurchaseOrder(date=datetime.date(2024, 1, 1), price=12.5, notes="boxes")
    assert repr(po) == "2024-01-01: boxes $12.50"


def test_supply_repr_includes_name_and_quantity():
    supply = Supply(name="Mailers", quantity=50)
    assert repr(supply) == "Mailers: 50"
