"""The Has Pictures filter and the Pictures column on the Units lists."""

import re

import pytest
from auth import signed_in_client

import fbc
from fbc.models.inventory import Unit
from fbc.models.pictures import UnitPicture


@pytest.fixture
def client(app):
    return signed_in_client(app)


def _unit(name, pictures, price=10.0):
    unit = Unit(name=name, retail_price=price)
    fbc.db.session.add(unit)
    fbc.db.session.commit()
    for n in range(pictures):
        fbc.db.session.add(
            UnitPicture(unit_id=unit.id, position=n + 1, file="f", original_file="o")
        )
    fbc.db.session.commit()
    return unit


def _names(client, url):
    html = client.get(url).get_data(as_text=True)
    return {n for n in ("Bare", "One", "Three", "Cheap") if ">" + n + "<" in html}


def test_filter_is_offered_in_search(client):
    html = client.get("/unitmodelview/list/").get_data(as_text=True)
    assert "Has Pictures" in html
    assert "has_pictures" in html


def test_yes_and_no(client):
    _unit("Bare", 0)
    _unit("One", 1)
    _unit("Three", 3)
    yes = _names(client, "/unitmodelview/list/?_flt_0_has_pictures=y")
    no = _names(client, "/unitmodelview/list/?_flt_0_has_pictures=n")
    assert yes == {"One", "Three"}
    assert no == {"Bare"}


def test_combines_with_a_task_lists_own_filter(client):
    _unit("Bare", 0)
    _unit("One", 1)
    _unit("Cheap", 1, price=1.0)  # under the task's price, so not in it
    url = "/unitsneedingdraftsview/list/"
    assert _names(client, url) == {"Bare", "One"}
    assert _names(client, url + "?_flt_0_has_pictures=y") == {"One"}
    assert _names(client, url + "?_flt_0_has_pictures=n") == {"Bare"}


def test_combines_with_archived(client):
    unit = _unit("One", 1)
    _unit("Three", 3)
    unit.archived = True
    fbc.db.session.commit()
    url = "/unitmodelview/list/?_flt_0_has_pictures=y"
    assert _names(client, url) == {"Three"}
    assert _names(client, url + "&archived=all") == {"One", "Three"}


def test_column_counts(client):
    _unit("Bare", 0)
    _unit("One", 1)
    _unit("Three", 3)
    html = client.get("/unitmodelview/list/").get_data(as_text=True)
    rows = re.findall(r"<tr>.*?</tr>", html, re.S)
    by_name = {}
    for row in rows:
        for name in ("Bare", "One", "Three"):
            if ">" + name + "<" in row:
                by_name[name] = row
    assert "<b>0</b>" in by_name["Bare"]
    assert "<b>0</b>" not in by_name["One"]
    assert "<b>0</b>" not in by_name["Three"]
    assert re.search(r">\s*3\s*<", by_name["Three"])
    assert re.search(r">\s*1\s*<", by_name["One"])


def test_counts_in_one_query_for_a_page(app):
    units = [_unit("U%d" % n, n) for n in range(4)]
    fbc.db.session.expire_all()
    units = Unit.query.all()
    Unit.prime_picture_counts(units)
    assert sorted(u._picture_count for u in units) == [0, 1, 2, 3]
