import datetime

from fbc.models.ebay import EbayOrder
from fbc.models.sales import SalesReceipt


def test_fmt_sold_price_and_net_sold_format_currency():
    receipt = SalesReceipt(
        date=datetime.date(2024, 1, 1), sold_price=25.5, net_sold=22.0
    )
    assert receipt.fmt_sold_price() == "$25.50"
    assert receipt.fmt_net_sold() == "$22.00"


def test_repr_without_ebay_order():
    receipt = SalesReceipt(
        date=datetime.date(2024, 1, 1), sold_price=25.5, net_sold=22.0
    )
    assert repr(receipt) == "2024-01-01 $25.50/$22.00"


def test_repr_with_ebay_order_includes_order_details():
    order = EbayOrder(
        order_id="111-222",
        created_time=datetime.datetime(2024, 1, 1),
        buyer="buyer1",
        price=25.5,
        title="A" * 50,
    )
    receipt = SalesReceipt(
        date=datetime.date(2024, 1, 1), sold_price=25.5, net_sold=22.0, ebay_order=order
    )
    text = repr(receipt)
    assert "111-222" in text
    assert order.title[:45] in text
    assert "$25.50/$22.00" in text


def test_fmt_ebay_order_renders_link_when_order_present(app):
    order = EbayOrder(
        order_id="111-222",
        created_time=datetime.datetime(2024, 1, 1),
        buyer="buyer1",
        price=25.5,
        title="Item",
    )
    receipt = SalesReceipt(
        date=datetime.date(2024, 1, 1),
        sold_price=25.5,
        net_sold=22.0,
        ebay_order_id="111-222",
        ebay_order=order,
    )
    html = receipt.fmt_ebay_order(markup=False)
    assert "111-222" in html
    assert "$25.50" in html


def test_fmt_ebay_order_empty_without_order_id(app):
    receipt = SalesReceipt(date=datetime.date(2024, 1, 1), sold_price=1, net_sold=1)
    assert receipt.fmt_ebay_order(markup=False) == ""


def test_link_column_delegates_to_fmt_ebay_order(app):
    order = EbayOrder(
        order_id="333",
        created_time=datetime.datetime(2024, 1, 1),
        buyer="buyer",
        price=10.0,
        title="Item",
    )
    receipt = SalesReceipt(
        date=datetime.date(2024, 1, 1),
        sold_price=10.0,
        net_sold=9.0,
        ebay_order_id="333",
        ebay_order=order,
    )
    assert "333" in str(receipt.link_column())
