"""
List columns that sort by something other than themselves (#228).

The lists are asked for in order through the same URL arguments the column
header links use, and the order is read back from the page. Rows with
nothing in the sort column come last, going up or down.
"""

import datetime
import re

import pytest

import fbc
from fbc.models.ebay import EbayListing, EbayOrder
from fbc.models.discogs import (
    Artist,
    DiscogsListing,
    DiscogsRelease,
    Folder,
    Genre,
    Style,
)
from fbc.models.inventory import PurchaseLot, StorageBox, Unit
from fbc.models.sales import ListingEnd
from fbc.models.supplies import Supply, SupplyUse
from fbc.models.sales import SalesReceipt
from fbc.views.discogs import DiscogsListingModelView, DiscogsReleaseModelView
from fbc.views.inventory import (
    PurchaseLotModelView,
    UnitModelReboxView,
    UnitModelView,
)
from fbc.views.sales import (
    EbayListingModelView,
    EbayOrderModelView,
    ListingEndModelView,
    SalesReceiptModelView,
)
from fbc.views.supplies import SupplyUseModelView


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
    # Not the search form above the table, which can list the same names.
    html = html[html.index("table-bordered") :]
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


# Kind 2: a relationship to one item, sorted by a column of the item.


def test_units_sort_by_the_date_of_their_purchase_lot(client):
    # The names run the other way round from the dates.
    early = PurchaseLot(name="Zed lot", date=datetime.date(2023, 1, 1), price=1)
    late = PurchaseLot(name="Alpha lot", date=datetime.date(2024, 6, 1), price=1)
    add(
        early,
        late,
        unit("Unit-late", purchase_lot=late),
        unit("Unit-early", purchase_lot=early),
        unit("Unit-nolot"),
    )

    check_order(
        client,
        UnitModelView,
        "purchase_lot",
        ["Unit-early", "Unit-late"],
        empty=["Unit-nolot"],
    )


def test_units_sort_by_the_name_of_their_storage_box(client):
    first = StorageBox(name="Box A")
    second = StorageBox(name="Box B")
    add(
        first,
        second,
        unit("Unit-in-b", storage_box=second),
        unit("Unit-in-a", storage_box=first),
        unit("Unit-nobox"),
    )

    check_order(
        client,
        UnitModelView,
        "storage_box",
        ["Unit-in-a", "Unit-in-b"],
        empty=["Unit-nobox"],
    )


def test_units_with_no_lot_stay_in_the_list_when_sorted_by_lot(client):
    add(unit("Unit-only"))

    assert listed(client, UnitModelView, "purchase_lot", "asc", ["Unit-only"])


def test_purchase_lots_sort_by_the_name_of_their_discogs_folder(client):
    folder_a = Folder(folder_id=1, name="Folder A")
    folder_b = Folder(folder_id=2, name="Folder B")
    day = datetime.date(2024, 1, 1)
    add(
        folder_a,
        folder_b,
        PurchaseLot(name="Lot-in-b", date=day, price=1, folder=folder_b),
        PurchaseLot(name="Lot-in-a", date=day, price=1, folder=folder_a),
        PurchaseLot(name="Lot-nofolder", date=day, price=1),
    )

    check_order(
        client,
        PurchaseLotModelView,
        "folder",
        ["Lot-in-a", "Lot-in-b"],
        empty=["Lot-nofolder"],
    )


def test_discogs_listings_sort_by_their_unit(client):
    unit_a, unit_b = unit("Unit-a"), unit("Unit-b")
    add(unit_a, unit_b)
    for listing_id, title, linked in (
        (1, "Listing-for-b", unit_b),
        (2, "Listing-for-a", unit_a),
        (3, "Listing-no-unit", None),
    ):
        add(DiscogsListing(listing_id=listing_id, title=title, unit=linked))

    check_order(
        client,
        DiscogsListingModelView,
        "unit",
        ["Listing-for-a", "Listing-for-b"],
        empty=["Listing-no-unit"],
    )


def test_listing_ends_sort_by_their_unit(client):
    unit_a, unit_b = unit("Unit-a"), unit("Unit-b")
    add(unit_a, unit_b)
    now = datetime.datetime(2024, 1, 1)
    for ref, linked in (("REF-for-b", unit_b), ("REF-for-a", unit_a)):
        add(
            ListingEnd(
                unit=linked,
                sold_on="eBay",
                ended_on="Discogs",
                listing_ref=ref,
                status="failed",
                created=now,
                updated=now,
            )
        )

    check_order(client, ListingEndModelView, "unit", ["REF-for-a", "REF-for-b"])


def test_supply_uses_sort_by_supply_name_and_unit(client):
    supplies = {n: Supply(name=n) for n in ("Supply-a", "Supply-b", "Supply-c")}
    unit_a, unit_b = unit("Unit-a"), unit("Unit-b")
    add(*supplies.values(), unit_a, unit_b)
    now = datetime.datetime(2024, 1, 1)
    for supply, linked in (
        ("Supply-b", unit_a),
        ("Supply-a", unit_b),
        ("Supply-c", None),
    ):
        add(
            SupplyUse(
                supply=supplies[supply], unit=linked, count=1, kind="ship", date=now
            )
        )

    check_order(
        client, SupplyUseModelView, "fmt_supply", ["Supply-a", "Supply-b", "Supply-c"]
    )
    check_order(
        client, SupplyUseModelView, "unit", ["Unit-a", "Unit-b"], empty=["Supply-c"]
    )


# Kind 3: a list of items, or a value worked out in SQL.


def release(release_id, title, **fields):
    return DiscogsRelease(release_id=release_id, title=title, **fields)


def test_releases_sort_by_their_first_artist_alphabetically(client):
    # Each release's artists are given out of order: the first one
    # alphabetically is the one that counts. The sort name wins over the name.
    zed = Artist(artist_id=1, name="Zed")
    amy = Artist(artist_id=2, name="Amy")
    mid = Artist(artist_id=3, name="The Middle", sort_name="Middle, The")
    bob = Artist(artist_id=4, name="Bob")
    add(
        zed,
        amy,
        mid,
        bob,
        release(1, "Rel-with-zed-and-bob", artists=[zed, bob]),
        release(2, "Rel-with-middle", artists=[mid]),
        release(3, "Rel-with-amy", artists=[amy]),
        release(4, "Rel-no-artist"),
    )

    check_order(
        client,
        DiscogsReleaseModelView,
        "artists",
        ["Rel-with-amy", "Rel-with-zed-and-bob", "Rel-with-middle"],
        empty=["Rel-no-artist"],
    )


def test_releases_sort_by_their_first_genre_and_style(client):
    rock, jazz, soul = Genre(name="Rock"), Genre(name="Jazz"), Genre(name="Soul")
    fuzz, bop = Style(name="Fuzz"), Style(name="Bop")
    add(
        rock,
        jazz,
        soul,
        fuzz,
        bop,
        release(1, "Rel-rock-jazz", genres=[rock, jazz], styles=[fuzz]),
        release(2, "Rel-soul", genres=[soul], styles=[bop]),
        release(3, "Rel-bare"),
    )

    check_order(
        client,
        DiscogsReleaseModelView,
        "genres",
        ["Rel-rock-jazz", "Rel-soul"],
        empty=["Rel-bare"],
    )
    check_order(
        client,
        DiscogsReleaseModelView,
        "styles",
        ["Rel-soul", "Rel-rock-jazz"],
        empty=["Rel-bare"],
    )


def test_releases_sort_by_the_purchase_lot_of_their_first_unit(client):
    early = PurchaseLot(name="Zed lot", date=datetime.date(2023, 1, 1), price=1)
    late = PurchaseLot(name="Alpha lot", date=datetime.date(2024, 6, 1), price=1)
    rel_early, rel_late = release(1, "Rel-early"), release(2, "Rel-late")
    rel_nolot, rel_nounit = release(3, "Rel-nolot"), release(4, "Rel-nounit")
    add(
        early,
        late,
        rel_early,
        rel_late,
        rel_nolot,
        rel_nounit,
        unit("Unit-1", discogs_release=rel_late, purchase_lot=late),
        unit("Unit-2", discogs_release=rel_early, purchase_lot=early),
        # A second Unit with a lot doesn't count: the first has none.
        unit("Unit-3", discogs_release=rel_nolot),
        unit("Unit-4", discogs_release=rel_nolot, purchase_lot=early),
    )

    check_order(
        client,
        DiscogsReleaseModelView,
        "purchase_lot",
        ["Rel-early", "Rel-late"],
        empty=["Rel-nolot", "Rel-nounit"],
    )


def test_rebox_units_sort_by_the_first_genre_and_style_of_their_release(client):
    rock, soul = Genre(name="Rock"), Genre(name="Soul")
    fuzz, bop = Style(name="Fuzz"), Style(name="Bop")
    rel_rock = release(1, "R1", genres=[rock], styles=[fuzz])
    rel_soul = release(2, "R2", genres=[soul], styles=[bop])
    add(
        rock,
        soul,
        fuzz,
        bop,
        rel_rock,
        rel_soul,
        unit("Unit-rock", discogs_release=rel_rock),
        unit("Unit-soul", discogs_release=rel_soul),
        unit("Unit-no-release"),
    )

    check_order(
        client,
        UnitModelReboxView,
        "discogs_genre_column",
        ["Unit-rock", "Unit-soul"],
        empty=["Unit-no-release"],
    )
    check_order(
        client,
        UnitModelReboxView,
        "discogs_style_column",
        ["Unit-soul", "Unit-rock"],
        empty=["Unit-no-release"],
    )
