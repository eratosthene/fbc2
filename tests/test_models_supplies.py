import datetime

import pytest

from fbc import db
from fbc.models.supplies import (
    ADDING_UNIT,
    ADJUSTMENT,
    RECONCILE,
    SHIPPING,
    PurchaseOrder,
    Supply,
    SupplyUse,
)


def test_purchase_order_purchase_price_formats_currency():
    po = PurchaseOrder(date=datetime.date(2024, 1, 1), price=12.5, notes="boxes")
    assert po.purchase_price() == "$12.50"


def test_purchase_order_repr_includes_date_notes_and_price():
    po = PurchaseOrder(date=datetime.date(2024, 1, 1), price=12.5, notes="boxes")
    assert repr(po) == "2024-01-01: boxes $12.50"


def test_supply_repr_includes_name_and_quantity():
    supply = Supply(name="Mailers", quantity=50)
    assert repr(supply) == "Mailers: 50"


def test_supply_use_takes_one_of_quantity_per_use():
    supply = Supply(name="LP Mailers", quantity=10, pack_size=1, pack_used=0)
    supply.use(3, SHIPPING)
    assert (supply.quantity, supply.pack_used) == (7, 0)


def test_supply_use_never_takes_quantity_below_zero():
    supply = Supply(name="LP Mailers", quantity=1, pack_size=1, pack_used=0)
    supply.use(3, SHIPPING)
    assert supply.quantity == 0


def test_supply_use_takes_a_roll_once_its_labels_run_out():
    supply = Supply(name="Shipping Label Roll", quantity=3, pack_size=220)
    supply.pack_used = 218
    supply.use(1, SHIPPING)
    assert (supply.quantity, supply.pack_used) == (3, 219)
    supply.use(2, SHIPPING)
    assert (supply.quantity, supply.pack_used) == (2, 1)


def test_supply_use_leaves_none_in_use_once_the_last_one_runs_out():
    supply = Supply(name="Shipping Label Roll", quantity=1, pack_size=220)
    supply.pack_used = 219
    supply.use(1, SHIPPING)
    assert (supply.quantity, supply.pack_used) == (0, 0)


@pytest.mark.parametrize(
    "quantity, pack_size, pack_used, available",
    [
        (3, 1, 0, 3),
        (2, 220, 219, 221),
        (1, 220, 0, 220),
        (0, 220, 0, 0),
        (0, 220, 5, 0),
    ],
)
def test_supply_available_counts_the_uses_left(
    quantity, pack_size, pack_used, available
):
    supply = Supply(
        name="S", quantity=quantity, pack_size=pack_size, pack_used=pack_used
    )
    assert supply.available() == available


def test_supply_use_works_on_a_supply_not_yet_saved():
    # pack_size and pack_used only get their defaults once saved.
    supply = Supply(name="LP Mailers", quantity=2)
    supply.use(1, SHIPPING)
    assert (supply.quantity, supply.pack_used) == (1, 0)


def test_supply_fmt_quantity_shows_whats_left_on_the_one_in_use():
    assert Supply(name="Mailers", quantity=5, pack_size=1).fmt_quantity() == "5"
    rolls = Supply(name="Labels", quantity=3, pack_size=220, pack_used=41)
    assert rolls.fmt_quantity() == "3 (179 of 220 left on the one in use)"
    # With none left, none is in use.
    assert Supply(name="Labels", quantity=0, pack_size=220).fmt_quantity() == "0"


def test_supply_shows_its_choices_by_their_labels():
    supply = Supply(name="S", used_for="shipping", record_size='7"')
    assert (supply.fmt_used_for(), supply.fmt_record_size()) == (
        "Shipping an order",
        '7"',
    )
    supply = Supply(name="S")
    assert (supply.fmt_used_for(), supply.fmt_record_size()) == ("Only by hand", "Any")


@pytest.mark.parametrize(
    "quantity, min_quantity, low",
    [(5, 0, False), (0, 0, False), (6, 5, False), (5, 5, True), (1, 5, True)],
)
def test_supply_is_low_once_down_to_its_minimum(quantity, min_quantity, low):
    supply = Supply(name="Mailers", quantity=quantity, min_quantity=min_quantity)
    assert supply.is_low() is low


def test_low_list_has_only_supplies_due_a_reorder(db):
    db.session.add_all(
        [
            Supply(name="Mailers", quantity=2, min_quantity=5),
            Supply(name="Tape", quantity=0, min_quantity=0),
            Supply(name="Labels", quantity=4, min_quantity=1),
            Supply(name="Inner Sleeves", quantity=1, min_quantity=1),
        ]
    )
    db.session.commit()
    assert [s.name for s in Supply.low_list()] == ["Inner Sleeves", "Mailers"]


@pytest.mark.parametrize(
    "per_units, record_size, sizes, count",
    [
        (None, None, ['12"'], 0),
        (5, None, ['12"'], 1),
        (5, None, ['12"'] * 5, 1),
        (5, None, ['12"'] * 6, 2),
        (1, '12"', ['12"', '7"', '12"'], 2),
        (1, '7"', ['12"'], 0),
        (0, None, ['12"', '7"'], 1),
        (0, '7"', ['12"'], 0),
    ],
)
def test_supply_shipping_count(per_units, record_size, sizes, count):
    supply = Supply(name="S", per_units=per_units, record_size=record_size)
    assert supply.shipping_count(sizes) == count


def test_supply_use_records_the_use():
    supply = Supply(name="LP Mailers", quantity=10)
    db.session.add(supply)
    db.session.commit()

    use = supply.use(2, ADDING_UNIT)
    db.session.commit()

    assert db.session.query(SupplyUse).one() is use
    assert (use.supply, use.count, use.kind) == (supply, 2, ADDING_UNIT)
    assert use.date is not None
    assert use.fmt_kind() == "Adding a Unit"


def _saved_labels():
    supply = Supply(name="Shipping Label Roll", quantity=3, pack_size=220, pack_used=10)
    db.session.add(supply)
    db.session.commit()
    return supply


@pytest.mark.parametrize(
    "changes, count",
    [
        # Two rolls short on a stock-take.
        ({"quantity": 1}, 440),
        # Five more labels gone than recorded.
        ({"pack_used": 15}, 5),
        # A restock of two rolls.
        ({"quantity": 5}, -440),
    ],
)
def test_record_adjustment_records_the_uses_a_change_made(changes, count):
    supply = _saved_labels()
    for name, value in changes.items():
        setattr(supply, name, value)

    use = supply.record_adjustment()

    assert (use.count, use.kind) == (count, ADJUSTMENT)
    db.session.commit()
    assert db.session.query(SupplyUse).count() == 1


@pytest.mark.parametrize(
    "changes",
    [{}, {"quantity": 3, "pack_used": 10}, {"pack_size": 200}, {"name": "Labels"}],
)
def test_record_adjustment_records_nothing_when_stock_didnt_change(changes):
    supply = _saved_labels()
    for name, value in changes.items():
        setattr(supply, name, value)

    assert supply.record_adjustment() is None
    db.session.commit()
    assert db.session.query(SupplyUse).count() == 0


def test_deleting_a_supply_deletes_its_uses():
    supply = _saved_labels()
    supply.use(1, SHIPPING)
    db.session.commit()

    db.session.delete(supply)
    db.session.commit()

    assert db.session.query(SupplyUse).count() == 0


def test_supply_fmt_cost_per_use_shows_dollars():
    assert Supply(name="Labels").fmt_cost_per_use() == ""
    assert Supply(name="Labels", cost_per_use=0.0455).fmt_cost_per_use() == "$0.0455"
    assert Supply(name="Mailers", cost_per_use=1.5).fmt_cost_per_use() == "$1.5"


@pytest.mark.parametrize(
    "on_hand, left, stock",
    [
        # 2 rolls, about 150 labels left on the open one.
        (2, 150, (2, 70)),
        # A fresh roll in use.
        (1, 220, (1, 0)),
        (3, 1, (3, 219)),
        # None on hand, so none in use.
        (0, None, (0, 0)),
        (0, 0, (0, 0)),
    ],
)
def test_stock_from_count_works_out_a_rolls_quantity_and_used(on_hand, left, stock):
    supply = Supply(name="Shipping Label Roll", quantity=1, pack_size=220)
    assert supply.stock_from_count(on_hand, left) == stock


@pytest.mark.parametrize("on_hand, stock", [(48, (48, 0)), (0, (0, 0))])
def test_stock_from_count_takes_on_hand_as_quantity_for_singles(on_hand, stock):
    supply = Supply(name="LP Mailers", quantity=51, pack_size=1)
    assert supply.stock_from_count(on_hand) == stock


@pytest.mark.parametrize(
    "on_hand, left, error",
    [
        (-1, None, "can't be below 0"),
        (2, None, "how many uses are left"),
        # Used up, so not on hand: Quantity would have gone down.
        (2, 0, "from 1 to 220"),
        (2, 221, "from 1 to 220"),
        (0, 5, "blank when none are on hand"),
    ],
)
def test_stock_from_count_refuses_what_the_supply_form_would(on_hand, left, error):
    supply = Supply(name="Shipping Label Roll", quantity=1, pack_size=220)
    with pytest.raises(ValueError, match=error):
        supply.stock_from_count(on_hand, left)


def test_record_adjustment_can_record_a_reconcile(db):
    supply = Supply(name="LP Mailers", quantity=51)
    db.session.add(supply)
    db.session.commit()

    supply.quantity = 48
    use = supply.record_adjustment(RECONCILE)

    assert (use.count, use.kind, use.fmt_kind()) == (3, RECONCILE, "Reconcile Supplies")
