import datetime

import pytest

from fbc import db
from fbc.models.expenses import (
    DEFAULT_CATEGORIES,
    Expense,
    categories,
    category_label,
    expense_totals,
    purchase_order_expense_total,
)
from fbc.models.supplies import PurchaseOrder


def _expenses(*rows):
    for date, category, amount in rows:
        db.session.add(
            Expense(date=datetime.date(*date), category=category, amount=amount)
        )
    db.session.commit()


def test_categories_default_to_schedule_c_lines():
    assert categories() == DEFAULT_CATEGORIES
    assert category_label("car") == "Car and truck (line 9)"


def test_categories_come_from_config_when_set(app, monkeypatch):
    monkeypatch.setitem(app.config, "EXPENSE_CATEGORIES", {"fairs": "Record fairs"})

    assert categories() == {"fairs": "Record fairs"}
    assert category_label("fairs") == "Record fairs"
    # One config.py no longer lists still shows, as its key.
    assert category_label("car") == "car"


def test_expense_totals_add_up_each_category_in_category_order():
    _expenses(
        ((2026, 1, 5), "other", 4.0),
        ((2026, 1, 9), "advertising", 10.0),
        ((2026, 2, 1), "advertising", 2.5),
        ((2026, 2, 3), "gone", 1.0),
    )

    assert expense_totals() == {"advertising": 12.5, "other": 4.0, "gone": 1.0}
    assert list(expense_totals()) == ["advertising", "other", "gone"]


def test_expense_totals_keep_to_the_dates_given():
    _expenses(
        ((2025, 12, 31), "fees", 30.0),
        ((2026, 1, 1), "fees", 1.0),
        ((2026, 3, 31), "fees", 2.0),
        ((2026, 4, 1), "fees", 40.0),
    )

    totals = expense_totals(datetime.date(2026, 1, 1), datetime.date(2026, 3, 31))

    assert totals == {"fees": 3.0}


def test_mileage_works_out_the_amount_and_keeps_the_rate():
    expense = Expense(miles=120.0)

    expense.work_out_mileage(0.725)

    assert (expense.amount, expense.mileage_rate) == (87.0, 0.725)
    assert expense.fmt_mileage_rate() == "$0.725"


def test_mileage_keeps_a_rate_typed_in():
    expense = Expense(miles=10.0, mileage_rate=0.655)

    expense.work_out_mileage(0.725)

    assert (expense.amount, expense.mileage_rate) == (6.55, 0.655)


def test_an_expense_without_miles_keeps_its_amount_and_no_rate():
    expense = Expense(amount=21.95, mileage_rate=0.7)

    expense.work_out_mileage(0.725)

    assert (expense.amount, expense.mileage_rate) == (21.95, None)


@pytest.mark.parametrize("amount, text", [(5.0, "$5.00"), (1234.5, "$1,234.50")])
def test_fmt_amount(amount, text):
    assert Expense(amount=amount).fmt_amount() == text


def _order(price=100.0, notes="Uline order"):
    order = PurchaseOrder(date=datetime.date(2026, 4, 1), price=price, notes=notes)
    db.session.add(order)
    return order


def test_a_purchase_orders_expenses_come_out_of_its_supply_cost():
    order = _order()
    for amount in (30.0, 12.5):
        db.session.add(
            Expense(
                date=datetime.date(2026, 4, 1),
                category="supplies",
                amount=amount,
                purchase_order=order,
            )
        )
    _expenses(((2026, 4, 2), "fees", 21.95))

    assert order.expense_total() == 42.5
    assert order.supply_cost() == 57.5
    assert order.fmt_supply_cost() == "$57.50"
    assert purchase_order_expense_total() == 42.5


def test_purchase_order_expense_total_is_0_with_none_on_orders():
    _expenses(((2026, 4, 2), "fees", 21.95))

    assert purchase_order_expense_total() == 0


def test_deleting_a_purchase_order_keeps_its_expenses():
    order = _order()
    expense = Expense(
        date=datetime.date(2026, 4, 1),
        category="equipment",
        amount=40.0,
        purchase_order=order,
    )
    db.session.add(expense)
    db.session.commit()

    db.session.delete(order)
    db.session.commit()

    assert db.session.query(Expense).one().purchase_order_id is None


def test_a_purchase_order_without_notes_still_shows():
    assert repr(_order(price=5.0, notes=None)) == "2026-04-01:  $5.00"
