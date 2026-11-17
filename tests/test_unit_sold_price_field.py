"""Sold Price is on Edit Unit, with a note, and not on Add Unit."""

import fbc
from auth import signed_in_client
from fbc.models.inventory import Unit


def _unit():
    unit = Unit(name="Some Unit", unit_type='12" LP', pressing="", grading="")
    fbc.db.session.add(unit)
    fbc.db.session.commit()
    return unit


def test_add_unit_has_no_sold_price_field():
    client = signed_in_client()

    html = client.get("/unitmodelview/add").get_data(as_text=True)

    assert 'name="sold_price"' not in html
    assert "Sold Price" not in html


def test_edit_unit_has_the_sold_price_field_with_a_note():
    client = signed_in_client()
    unit = _unit()

    html = client.get("/unitmodelview/edit/%d" % unit.id).get_data(as_text=True)

    assert 'name="sold_price"' in html
    assert "Sold Price" in html
    assert "Only change this if you need to." in html


def test_the_sold_price_saves_from_edit_unit():
    client = signed_in_client()
    unit = _unit()

    page = client.post(
        "/unitmodelview/edit/%d" % unit.id,
        data={"name": "Some Unit", "unit_type": '12" LP', "sold_price": "7.25"},
    )

    assert page.status_code == 302
    fbc.db.session.expire_all()
    assert fbc.db.session.get(Unit, unit.id).sold_price == 7.25
