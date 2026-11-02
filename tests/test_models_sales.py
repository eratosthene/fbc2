import datetime

from fbc.models.ebay import EbayOrder
from fbc.models.sales import SalesReceipt, SalesReceiptLine, earnings_totals


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
        date=datetime.date(2024, 1, 1),
        sold_price=25.5,
        net_sold=22.0,
        ebay_orders=[order],
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
        ebay_orders=[order],
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
        ebay_orders=[order],
    )
    assert "333" in str(receipt.link_column())


def _line(kind, amount, fee_type=None):
    return {"kind": kind, "label": kind.title(), "amount": amount, "fee_type": fee_type}


def _receipt(date, sold_price, net_sold, lines=()):
    receipt = SalesReceipt(date=date, sold_price=sold_price, net_sold=net_sold)
    receipt.set_lines(lines)
    return receipt


def test_lines_that_add_up_need_no_adjustment():
    receipt = _receipt(
        datetime.date(2026, 1, 1),
        25.0,
        16.5,
        [_line("SALE", 21.13), _line("SHIPPING_LABEL", -4.63)],
    )

    assert [line.kind for line in receipt.lines] == ["SALE", "SHIPPING_LABEL"]


def test_a_difference_from_net_sold_is_a_manual_adjustment_kept_up_to_date():
    receipt = _receipt(
        datetime.date(2026, 1, 1),
        25.0,
        16.0,
        [_line("SALE", 21.13), _line("SHIPPING_LABEL", -4.63)],
    )
    assert (receipt.lines[-1].kind, receipt.lines[-1].label) == (
        "MANUAL",
        "Manual adjustment",
    )
    assert receipt.lines[-1].amount == -0.5

    receipt.net_sold = 17.0
    receipt.balance_lines()
    assert [line.amount for line in receipt.lines] == [21.13, -4.63, 0.5]

    receipt.net_sold = 16.5
    assert receipt.balance_lines() is None
    assert [line.kind for line in receipt.lines] == ["SALE", "SHIPPING_LABEL"]


def test_a_receipt_with_no_lines_gets_no_adjustment():
    receipt = _receipt(datetime.date(2026, 1, 1), 25.0, 16.5)

    assert receipt.lines == []


def test_deleting_a_receipt_deletes_its_lines(db):
    receipt = _receipt(datetime.date(2026, 1, 1), 25.0, 16.5, [_line("SALE", 16.5)])
    db.session.add(receipt)
    db.session.commit()

    db.session.delete(receipt)
    db.session.commit()

    assert db.session.query(SalesReceiptLine).count() == 0


def test_earnings_totals_split_a_periods_receipts_by_kind_of_line(db):
    db.session.add_all(
        [
            _receipt(
                datetime.date(2026, 3, 1),
                25.0,
                7.6,
                [
                    _line("SALE", 25.3),
                    _line("FEE", -3.67, "FINAL_VALUE_FEE"),
                    _line("FEE", -1.0, "AD_FEE"),
                    _line("SHIPPING_LABEL", -4.63),
                    _line("REFUND", -5.0),
                    _line("NON_SALE_CHARGE", -1.0),
                    # Billed apart from the sale, but still eBay's fees.
                    _line("NON_SALE_CHARGE", -0.4, "FINAL_VALUE_FEE_FIXED_PER_ORDER"),
                    _line("NON_SALE_CHARGE", -2.0, "PREMIUM_AD_FEES"),
                ],
            ),
            # No lines: its fees are only known as gross less net.
            _receipt(datetime.date(2026, 3, 15), 30.0, 24.0),
            # Outside the period.
            _receipt(datetime.date(2026, 4, 1), 50.0, 40.0, [_line("SALE", 40.0)]),
        ]
    )
    db.session.commit()

    totals = earnings_totals(datetime.date(2026, 3, 1), datetime.date(2026, 3, 31))

    assert totals == {
        "receipts": 2,
        "gross": 55.0,
        "net": 31.6,
        "sales": 25.3,
        "ebay_fees": -4.07,
        "ad_fees": -3.0,
        "labels": -4.63,
        "refunds": -5.0,
        "other_fees": -1.0,
        "adjustments": 0.0,
        "other": 0.0,
        "not_broken_down": 6.0,
    }
    assert earnings_totals()["receipts"] == 3
    assert earnings_totals(start=datetime.date(2026, 4, 1))["sales"] == 40.0
