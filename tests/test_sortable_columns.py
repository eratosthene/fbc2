"""
List columns that sort by something other than themselves (#228).

The lists are asked for in order through the same URL arguments the column
header links use, and the order is read back from the page. Rows with
nothing in the sort column come last, going up or down.
"""

import re

import pytest

import fbc
from fbc.models.ebay import EbayListing, EbayOrder
from fbc.models.inventory import Unit
from fbc.models.sales import SalesReceipt
from fbc.views.inventory import UnitModelView
from fbc.views.sales import (
    EbayListingModelView,
    EbayOrderModelView,
    SalesReceiptModelView,
)


@pytest.fixture
def client():
    """A test client logged in as an Admin."""
    sm = fbc.appbuilder.sm
    sm.create_db()
    fbc.appbuilder.add_permissions(update_perms=True)
    sm.add_user("admin", "A", "Dmin", "admin@example.com", sm.find_role("Admin"), "pw")
    client = fbc.app.test_client()
    client.post("/login/", data={"username": "admin", "password": "pw"})
    return client


def _view(view_class):
    for view in fbc.appbuilder.baseviews:
        if type(view) is view_class:
            return view
    raise AssertionError(f"{view_class.__name__} is not registered")


def listed(client, view_class, column, direction, names):
    """Which of `names` the list shows, in the order it shows them."""
    name = view_class.__name__
    response = client.get(
        f"{_view(view_class).route_base}/list/",
        query_string={f"_oc_{name}": column, f"_od_{name}": direction},
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    found = {n: html.find(n) for n in names}
    assert all(i >= 0 for i in found.values()), found
    return sorted(names, key=found.get)


def has_sort_link(client, view_class, column):
    name = view_class.__name__
    html = client.get(f"{_view(view_class).route_base}/list/").get_data(as_text=True)
    return re.search(rf"_oc_{name}={column}\b", html) is not None


def check_order(client, view_class, column, ascending, empty=()):
    """Sorting by `column` gives `ascending`, reversed for down, `empty` last."""
    names = list(ascending) + list(empty)
    assert has_sort_link(client, view_class, column)
    assert listed(client, view_class, column, "asc", names) == (
        list(ascending) + list(empty)
    )
    assert listed(client, view_class, column, "desc", names) == (
        list(reversed(ascending)) + list(empty)
    )


def add(*rows):
    fbc.db.session.add_all(rows)
    fbc.db.session.commit()


def unit(name, **fields):
    return Unit(name=name, unit_type='12" LP', pressing="", grading="", **fields)


# Kind 1: a method that formats a real column.


def test_units_sort_by_retail_price_with_none_last(client):
    add(
        unit("Unit-mid", retail_price=20.0),
        unit("Unit-low", retail_price=5.0),
        unit("Unit-high", retail_price=100.0),
        unit("Unit-none"),
    )

    check_order(
        client,
        UnitModelView,
        "fmt_retail_price",
        ["Unit-low", "Unit-mid", "Unit-high"],
        empty=["Unit-none"],
    )


def test_ebay_listings_sort_by_price_not_by_the_dollar_text(client):
    # As text "$9.00" sorts after "$10.00"; as a number it sorts before.
    for item_id, price in (("L-ten", 10.0), ("L-nine", 9.0), ("L-hundred", 100.0)):
        add(EbayListing(item_id=item_id, title=item_id, price=price, url="https://x"))

    check_order(
        client,
        EbayListingModelView,
        "fmt_price",
        ["L-nine", "L-ten", "L-hundred"],
    )


def test_ebay_orders_sort_by_title_and_price_with_none_last(client):
    add(
        EbayOrder(order_id="O-1", buyer="b", title="Bravo title", price=30.0),
        EbayOrder(order_id="O-2", buyer="b", title="Alpha title", price=40.0),
        EbayOrder(order_id="O-3", buyer="b", title="Charlie title", price=2.0),
        EbayOrder(order_id="O-4", buyer="b", title=None, price=99.0),
    )
    titles = ["Alpha title", "Bravo title", "Charlie title"]

    # The list shows the title in the cell, and the id in the link.
    check_order(client, EbayOrderModelView, "fmt_title", titles, empty=["O-4"])
    check_order(client, EbayOrderModelView, "fmt_price", ["O-3", "O-1", "O-2", "O-4"])


def test_sales_receipts_sort_by_sold_price_and_net_sold(client):
    import datetime

    def receipt(sold, net):
        return SalesReceipt(
            date=datetime.date(2024, 1, 1),
            sold_price=sold,
            net_sold=net,
            notes="R" + str(sold),
        )

    add(receipt(50.0, 1.0), receipt(7.0, 3.0), receipt(20.0, 2.0))
    # The list shows the amounts, so read those back.
    check_order(
        client, SalesReceiptModelView, "fmt_sold_price", ["$7.00", "$20.00", "$50.00"]
    )
    check_order(
        client, SalesReceiptModelView, "fmt_net_sold", ["$1.00", "$2.00", "$3.00"]
    )
