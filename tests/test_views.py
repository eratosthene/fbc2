import pytest
from werkzeug.datastructures import MultiDict

import fbc
from fbc.models.ebay import EbayListing
from fbc.models.inventory import Unit
from fbc.models.supplies import Supply
from fbc.views.discogs import ArtistModelView, DiscogsReleaseModelView, FolderModelView
from fbc.views.inventory import (
    UNITS_WITHOUT_LISTING_FILTERS,
    UNITS_WITHOUT_MASTER_FILTERS,
    UnitModelReboxView,
    UnitModelView,
)
from fbc.views.sales import EbayListingModelView, EbayOrderModelView


def _view(view_class):
    for view in fbc.appbuilder.baseviews:
        if isinstance(view, view_class):
            return view
    raise AssertionError(f"{view_class.__name__} is not registered")


def test_artist_add_form_includes_primary_key():
    view = _view(ArtistModelView)
    form = view.add_form(
        MultiDict({"artist_id": "1", "name": "Some Artist", "sort_name": ""})
    )
    assert form.validate(), form.errors
    assert form.artist_id.data == 1


def test_folder_add_form_includes_primary_key():
    view = _view(FolderModelView)
    form = view.add_form(MultiDict({"folder_id": "1", "name": "Some Folder"}))
    assert form.validate(), form.errors
    assert form.folder_id.data == 1


def test_discogs_release_add_form_includes_primary_key():
    view = _view(DiscogsReleaseModelView)
    form = view.add_form(
        MultiDict(
            {
                "release_id": "1",
                "title": "Some Album",
                "year": "2020",
                "master_id": "0",
                "master_year": "2020",
                "released": "2020-01-01",
            }
        )
    )
    assert form.validate(), form.errors
    assert form.release_id.data == 1


def test_ebay_listing_add_form_accepts_full_datetime():
    view = _view(EbayListingModelView)
    form = view.add_form(
        MultiDict(
            {
                "item_id": "ITEM-1",
                "title": "Some Listing",
                "price": "9.99",
                "url": "https://example.com",
                "start_time": "2024-02-01T09:30:00",
            }
        )
    )
    assert form.validate(), form.errors
    assert form.start_time.data.isoformat() == "2024-02-01T09:30:00"


def test_ebay_listing_add_form_allows_blank_start_time():
    view = _view(EbayListingModelView)
    form = view.add_form(
        MultiDict(
            {
                "item_id": "ITEM-1",
                "title": "Some Listing",
                "price": "9.99",
                "url": "https://example.com",
                "start_time": "",
            }
        )
    )
    assert form.validate(), form.errors
    assert form.start_time.data is None


def test_ebay_order_add_form_accepts_full_datetime():
    view = _view(EbayOrderModelView)
    form = view.add_form(
        MultiDict(
            {
                "order_id": "ORD-1",
                "created_time": "2024-01-15T13:45:00",
                "buyer": "someone",
                "price": "12.50",
                "title": "Some Item",
            }
        )
    )
    assert form.validate(), form.errors
    assert form.created_time.data.isoformat() == "2024-01-15T13:45:00"


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


def _units(**units):
    for name, fields in units.items():
        fbc.db.session.add(
            Unit(name=name, unit_type='12" LP', pressing="", grading="", **fields)
        )
    fbc.db.session.commit()


def _menu_list(client, name):
    url = fbc.appbuilder.menu.find(name).get_url()
    response = client.get(url)
    assert response.status_code == 200
    return response.get_data(as_text=True)


@pytest.mark.parametrize(
    "filters, expected",
    [
        (
            UNITS_WITHOUT_LISTING_FILTERS,
            {
                "ebay_listing": "FilterRelationOneToManyEqual",
                "sold": "FilterNotEqual",
                "retail_price": "FilterGreater",
            },
        ),
        (UNITS_WITHOUT_MASTER_FILTERS, {"discogs_master": "FilterEqual"}),
    ],
)
def test_units_without_presets_pick_the_intended_filters(filters, expected):
    search_filters = _view(UnitModelView)._filters.get_search_filters()
    picked = {}
    for arg in filters:
        index, column = arg.removeprefix("_flt_").split("_", 1)
        picked[column] = type(search_filters[column][int(index)]).__name__
    assert picked == expected


def test_units_without_listings_menu_lists_unsold_unlisted_units_over_2(client):
    fbc.db.session.add(
        EbayListing(item_id="L1", title="T", price=9.99, url="https://x")
    )
    _units(
        WantedUnit={"sold": False, "retail_price": 9.99},
        ListedUnit={"sold": False, "retail_price": 9.99, "ebay_listing_id": "L1"},
        SoldUnit={"sold": True, "retail_price": 9.99},
        CheapUnit={"sold": False, "retail_price": 2.0},
    )

    html = _menu_list(client, "Units w/out Listings")

    assert "WantedUnit" in html
    assert "ListedUnit" not in html
    assert "SoldUnit" not in html
    assert "CheapUnit" not in html


def test_units_without_master_menu_lists_units_with_an_empty_master(client):
    _units(BlankMaster={"discogs_master": ""}, HasMaster={"discogs_master": "123"})

    html = _menu_list(client, "Units w/out Master")

    assert "BlankMaster" in html
    assert "HasMaster" not in html


@pytest.mark.parametrize("view_class", [UnitModelView, UnitModelReboxView])
def test_unit_forms_save_a_blank_master_and_unticked_sold(view_class):
    view = _view(view_class)
    form = view.add_form(MultiDict({"name": "Some Unit", "unit_type": '12" LP'}))
    assert form.validate(), form.errors
    unit = Unit()
    form.populate_obj(unit)
    fbc.db.session.add(unit)
    fbc.db.session.commit()
    assert (unit.discogs_master, unit.sold) == ("", False)


def _inner_sleeves():
    sleeves = Supply(name='Vinyl Inner Sleeves, 12"', quantity=50, used_for="unit")
    mailers = Supply(name="LP Mailers", quantity=10, used_for="shipping")
    fbc.db.session.add_all([sleeves, mailers])
    fbc.db.session.commit()
    return sleeves, mailers


def test_add_unit_offers_the_supplies_used_for_adding_a_unit(client):
    sleeves, mailers = _inner_sleeves()

    html = client.get("/unitmodelview/add").get_data(as_text=True)

    assert "Supplies Used" in html
    assert 'name="supplies_used-' + str(sleeves.id) + '"' in html
    assert "Vinyl Inner Sleeves, 12&#34;" in html
    assert "supplies_used-" + str(mailers.id) not in html


def test_adding_a_unit_takes_its_supplies_out_of_stock(client):
    sleeves, mailers = _inner_sleeves()

    page = client.post(
        "/unitmodelview/add",
        data={
            "name": "Some Unit",
            "unit_type": '12" LP',
            "supplies_used-" + str(sleeves.id): "2",
        },
    )

    assert page.status_code == 302
    fbc.db.session.expire_all()
    assert fbc.db.session.query(Unit).one().name == "Some Unit"
    assert (sleeves.quantity, mailers.quantity) == (48, 10)


def test_adding_a_unit_needs_whole_supply_counts(client):
    sleeves, mailers = _inner_sleeves()

    page = client.post(
        "/unitmodelview/add",
        data={
            "name": "Some Unit",
            "unit_type": '12" LP',
            "supplies_used-" + str(sleeves.id): "lots",
        },
    )

    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "as a whole number" in html
    assert 'value="lots"' in html
    fbc.db.session.expire_all()
    assert fbc.db.session.query(Unit).count() == 0
    assert sleeves.quantity == 50


def test_editing_a_unit_doesnt_take_supplies(client):
    sleeves, mailers = _inner_sleeves()
    _units(Old={})
    unit = fbc.db.session.query(Unit).one()

    html = client.get("/unitmodelview/edit/" + str(unit.id)).get_data(as_text=True)

    assert "supplies_used" not in html


def test_supply_form_saves_blank_choices_as_null(client):
    client.post(
        "/supplymodelview/add",
        data={
            "name": "Record Brushes",
            "quantity": "3",
            "min_quantity": "0",
            "used_for": "",
            "record_size": "",
            "pack_size": "1",
            "pack_used": "0",
        },
    )
    client.post(
        "/supplymodelview/add",
        data={
            "name": 'Vinyl Outer Sleeves, 7"',
            "quantity": "100",
            "min_quantity": "20",
            "used_for": "shipping",
            "record_size": '7"',
            "per_units": "1",
            "pack_size": "1",
            "pack_used": "0",
        },
    )

    supplies = fbc.db.session.query(Supply).order_by(Supply.id).all()
    assert [
        (s.name, s.used_for, s.record_size, s.per_units, s.min_quantity)
        for s in supplies
    ] == [
        ("Record Brushes", None, None, None, 0),
        ('Vinyl Outer Sleeves, 7"', "shipping", '7"', 1, 20),
    ]
    edit = client.get("/supplymodelview/edit/" + str(supplies[0].id))
    assert edit.status_code == 200
