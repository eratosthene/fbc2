import datetime

import pytest

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


def test_supply_use_takes_one_of_quantity_per_use():
    supply = Supply(name="LP Mailers", quantity=10, pack_size=1, pack_used=0)
    supply.use(3)
    assert (supply.quantity, supply.pack_used) == (7, 0)


def test_supply_use_never_takes_quantity_below_zero():
    supply = Supply(name="LP Mailers", quantity=1, pack_size=1, pack_used=0)
    supply.use(3)
    assert supply.quantity == 0


def test_supply_use_takes_a_roll_once_its_labels_run_out():
    supply = Supply(name="Shipping Label Roll", quantity=3, pack_size=220)
    supply.pack_used = 218
    supply.use(1)
    assert (supply.quantity, supply.pack_used) == (3, 219)
    supply.use(2)
    assert (supply.quantity, supply.pack_used) == (2, 1)


def test_supply_use_works_on_a_supply_not_yet_saved():
    # pack_size and pack_used only get their defaults once saved.
    supply = Supply(name="LP Mailers", quantity=2)
    supply.use(1)
    assert (supply.quantity, supply.pack_used) == (1, 0)


def test_supply_fmt_quantity_shows_whats_left_on_the_one_in_use():
    assert Supply(name="Mailers", quantity=5, pack_size=1).fmt_quantity() == "5"
    rolls = Supply(name="Labels", quantity=3, pack_size=220, pack_used=41)
    assert rolls.fmt_quantity() == "3 (179 of 220 left on the one in use)"


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
