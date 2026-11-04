import datetime
import re

import pytest
from werkzeug.datastructures import MultiDict

import fbc
from fbc.models.discogs import DiscogsRelease
from fbc.models.ebay import EbayListing
from fbc.models.expenses import Expense
from fbc.models.inventory import PurchaseLot, Unit
from fbc.models.supplies import PurchaseOrder, Supply, SupplyUse
from fbc.views.discogs import ArtistModelView, DiscogsReleaseModelView, FolderModelView
from fbc.views.inventory import (
    UNITS_WITHOUT_LISTING_FILTERS,
    UNITS_WITHOUT_MASTER_FILTERS,
    UNSOLD_UNITS_OVER_2_FILTERS,
    PurchaseLotModelView,
    UnitModelReboxView,
    UnitModelView,
)
from fbc.views.sales import EbayListingModelView, EbayOrderModelView


def _view(view_class):
    for view in fbc.appbuilder.baseviews:
        if type(view) is view_class:
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
    "view_class, filters, expected",
    [
        (
            UnitModelView,
            UNITS_WITHOUT_LISTING_FILTERS,
            {
                "ebay_listing": "FilterRelationOneToManyEqual",
                "sold": "FilterNotEqual",
                "retail_price": "FilterGreater",
            },
        ),
        (
            UnitModelView,
            UNITS_WITHOUT_MASTER_FILTERS,
            {"discogs_master": "FilterEqual"},
        ),
        (
            UnitModelReboxView,
            UNSOLD_UNITS_OVER_2_FILTERS,
            {"sold": "FilterNotEqual", "retail_price": "FilterGreater"},
        ),
    ],
)
def test_menu_presets_pick_the_intended_filters(view_class, filters, expected):
    search_filters = _view(view_class)._filters.get_search_filters()
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
    unit = fbc.db.session.query(Unit).one()
    assert unit.name == "Some Unit"
    assert (sleeves.quantity, mailers.quantity) == (48, 10)
    use = fbc.db.session.query(SupplyUse).one()
    assert (use.supply, use.count, use.kind, use.unit) == (sleeves, 2, "unit", unit)


def _flashes(client):
    with client.session_transaction() as session:
        return session.get("_flashes", [])


def test_adding_a_unit_without_a_retail_price_saves_it_with_a_warning(client):
    page = client.post(
        "/unitmodelview/add", data={"name": "Some Unit", "unit_type": '12" LP'}
    )

    assert page.status_code == 302
    unit = fbc.db.session.query(Unit).one()
    assert unit.retail_price is None
    warning = (
        'Some Unit has no Retail Price. <a href="/unitmodelview/edit/'
        + str(unit.id)
        + '">Edit it</a> to add one.'
    )
    assert ("warning", warning) in _flashes(client)


def test_adding_a_unit_with_a_retail_price_gives_no_warning(client):
    page = client.post(
        "/unitmodelview/add",
        data={"name": "Some Unit", "unit_type": '12" LP', "retail_price": "12.50"},
    )

    assert page.status_code == 302
    assert fbc.db.session.query(Unit).one().retail_price == 12.5
    assert not any("Retail Price" in message for _, message in _flashes(client))


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


def test_adding_a_unit_is_blocked_when_a_supply_would_run_out(client):
    sleeves, mailers = _inner_sleeves()

    page = client.post(
        "/unitmodelview/add",
        data={
            "name": "Some Unit",
            "unit_type": '12" LP',
            "supplies_used-" + str(sleeves.id): "51",
        },
    )

    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "it uses 51, and the Supply List has 50 left" in html
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


def _labels(client, **fields):
    data = {
        "name": "Shipping Label Roll",
        "quantity": "2",
        "min_quantity": "0",
        "used_for": "shipping",
        "record_size": "",
        "per_units": "0",
        "pack_size": "220",
        "pack_used": "0",
    }
    data.update(fields)
    return client.post("/supplymodelview/add", data=data)


@pytest.mark.parametrize(
    "fields, error",
    [
        ({"pack_used": "220"}, "Must be less than Uses Each"),
        ({"quantity": "0", "pack_used": "5"}, "Must be 0 when Quantity is 0"),
    ],
)
def test_supply_form_keeps_used_from_the_one_in_use_in_range(client, fields, error):
    page = _labels(client, **fields)

    assert error in page.get_data(as_text=True)
    assert fbc.db.session.query(Supply).count() == 0


def test_supply_list_shows_used_for_by_its_label(client):
    _labels(client, pack_used="219")
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

    html = client.get("/supplymodelview/list/").get_data(as_text=True)

    assert "Shipping an order" in html and "Only by hand" in html
    assert "2 (1 of 220 left on the one in use)" in html
    assert ">shipping<" not in html and ">None<" not in html


def _edit_labels(client, supply, **fields):
    data = {
        "name": supply.name,
        "quantity": str(supply.quantity),
        "min_quantity": "0",
        "used_for": "shipping",
        "record_size": "",
        "per_units": "0",
        "pack_size": "220",
        "pack_used": str(supply.pack_used),
        "cost_per_use": "",
    }
    data.update(fields)
    return client.post("/supplymodelview/edit/" + str(supply.id), data=data)


def test_supply_list_edit_records_a_stock_change(client):
    _labels(client, pack_used="10")
    supply = fbc.db.session.query(Supply).one()

    _edit_labels(client, supply, quantity="1", pack_used="20")

    fbc.db.session.expire_all()
    use = fbc.db.session.query(SupplyUse).one()
    assert (use.supply, use.count, use.kind) == (supply, 230, "adjustment")
    assert (supply.quantity, supply.pack_used) == (1, 20)


def test_supply_list_edit_records_nothing_when_stock_is_unchanged(client):
    _labels(client, pack_used="10")
    supply = fbc.db.session.query(Supply).one()

    _edit_labels(client, supply, min_quantity="1", cost_per_use="0.05")

    fbc.db.session.expire_all()
    assert fbc.db.session.query(SupplyUse).count() == 0
    assert (supply.min_quantity, supply.cost_per_use) == (1, 0.05)


def test_supply_page_and_supply_use_list_show_its_uses(client):
    _labels(client)
    supply = fbc.db.session.query(Supply).one()
    supply.use(3, "shipping")
    fbc.db.session.commit()

    show = client.get("/supplymodelview/show/" + str(supply.id))
    uses = _menu_list(client, "Supply Use")

    assert "Supply Use" in show.get_data(as_text=True)
    for html in (show.get_data(as_text=True), uses):
        assert "Shipping an order" in html
        assert "<td>3</td>" in html or ">3<" in html
    # The name alone: "Name: quantity" would be today's quantity.
    assert re.search(r"<td>\s*Shipping Label Roll\s*</td>", uses)


def test_supply_use_cant_be_added_or_changed(client):
    assert client.get("/supplyusemodelview/add").status_code in (302, 403, 404)
    assert client.get("/supplyusemodelview/list/").status_code == 200


def test_rebox_units_lists_the_columns_for_choosing_a_box(client):
    _units(SomeUnit={"sold": False, "retail_price": 9.99})

    units = _menu_list(client, "Units")
    rebox = _menu_list(client, "Rebox Units")

    assert "SomeUnit" in units and "SomeUnit" in rebox
    assert "<th>Retail Price</th>" in units
    assert "<th>Retail Price</th>" not in rebox
    for column in ("<th>Genres</th>", "<th>Styles</th>"):
        assert column in rebox
        assert column not in units


def test_rebox_units_menu_lists_unsold_units_over_2(client):
    _units(
        WantedUnit={"sold": False, "retail_price": 9.99},
        SoldUnit={"sold": True, "retail_price": 9.99},
        CheapUnit={"sold": False, "retail_price": 2.0},
    )

    html = _menu_list(client, "Rebox Units")

    assert "WantedUnit" in html
    assert "SoldUnit" not in html
    assert "CheapUnit" not in html


def test_releases_without_units_lists_only_releases_with_no_unit(client):
    for release_id, title in [(1, "UnitedAlbum"), (2, "LoneAlbum")]:
        fbc.db.session.add(
            DiscogsRelease(release_id=release_id, title=title, year=2020, master_id=0)
        )
    lot = PurchaseLot(name="Lot", date=datetime.date(2020, 1, 1), price=1.0)
    _units(SomeUnit={"discogs_release_id": 1, "purchase_lot": lot})

    releases = _menu_list(client, "Releases")
    without_units = _menu_list(client, "Releases w/out Units")

    assert "UnitedAlbum" in releases and "LoneAlbum" in releases
    assert "UnitedAlbum" not in without_units and "LoneAlbum" in without_units


@pytest.mark.parametrize(
    "view_class, related",
    [
        (PurchaseLotModelView, [UnitModelView]),
        (ArtistModelView, [DiscogsReleaseModelView]),
    ],
)
def test_related_views_leave_out_the_list_variants(view_class, related):
    inner = _view(view_class).get_init_inner_views()
    assert [type(view) for view in inner] == related


def _reconcile_supplies():
    """Mailers, a label roll and a supply only ever changed by hand."""
    supplies = [
        Supply(name="LP Mailers", quantity=51, used_for="shipping", per_units=5),
        Supply(
            name="Shipping Label Roll",
            quantity=2,
            used_for="shipping",
            per_units=0,
            pack_size=220,
            pack_used=10,
        ),
        Supply(name="Record Brushes", quantity=3),
    ]
    fbc.db.session.add_all(supplies)
    fbc.db.session.commit()
    return supplies


def _reconcile(client, **fields):
    return client.post("/reconcilesupplies/", data=fields).get_data(as_text=True)


def test_reconcile_supplies_lists_every_supply_with_its_stock(client):
    mailers, labels, brushes = _reconcile_supplies()

    html = _menu_list(client, "Reconcile Supplies")

    for supply in (mailers, labels, brushes):
        assert 'name="on_hand-' + str(supply.id) + '"' in html
        assert supply.fmt_quantity() in html
    assert "Only by hand" in html
    # Only the roll asks for uses left on the one in use.
    assert 'name="left-' + str(labels.id) + '"' in html
    assert 'name="left-' + str(mailers.id) + '"' not in html


def test_reconcile_supplies_shows_the_differences_before_saving(client):
    mailers, labels, brushes = _reconcile_supplies()

    html = _reconcile(
        client,
        action="review",
        **{
            "on_hand-" + str(mailers.id): "48",
            "on_hand-" + str(labels.id): "2",
            "left-" + str(labels.id): "150",
        },
    )

    assert re.search(
        r"<td>LP Mailers</td>\s*<td>51</td>\s*<td>48</td>\s*<td>−3</td>", html
    )
    assert "2 (150 of 220 left on the one in use)" in html
    assert "−60 uses" in html
    assert "Record Brushes" not in html
    assert 'name="seen-' + str(mailers.id) + '" value="51/0"' in html
    fbc.db.session.expire_all()
    assert mailers.quantity == 51
    assert fbc.db.session.query(SupplyUse).count() == 0


def test_reconcile_supplies_saves_only_the_supplies_filled_in(client):
    mailers, labels, brushes = _reconcile_supplies()

    response = client.post(
        "/reconcilesupplies/",
        data={
            "action": "save",
            "on_hand-" + str(mailers.id): "48",
            "seen-" + str(mailers.id): "51/0",
            "on_hand-" + str(labels.id): "3",
            "left-" + str(labels.id): "150",
            "seen-" + str(labels.id): "2/10",
            "on_hand-" + str(brushes.id): "",
        },
    )

    assert response.status_code == 302
    fbc.db.session.expire_all()
    assert mailers.quantity == 48
    assert (labels.quantity, labels.pack_used) == (3, 70)
    assert brushes.quantity == 3
    uses = fbc.db.session.query(SupplyUse).order_by(SupplyUse.supply_id).all()
    # A count that found more than the Supply List had counts below 0.
    assert [(u.supply, u.count, u.kind) for u in uses] == [
        (mailers, 3, "reconcile"),
        (labels, -160, "reconcile"),
    ]
    flashed = client.get(response.location).get_data(as_text=True)
    assert "LP Mailers: 51 → 48" in flashed


def test_reconcile_supplies_wont_save_if_stock_changed_since_the_review(client):
    mailers, labels, brushes = _reconcile_supplies()

    html = _reconcile(
        client,
        action="save",
        **{"on_hand-" + str(mailers.id): "48", "seen-" + str(mailers.id): "52/0"},
    )

    assert "LP Mailers changed since you checked the differences" in html
    assert 'name="seen-' + str(mailers.id) + '" value="51/0"' in html
    fbc.db.session.expire_all()
    assert mailers.quantity == 51
    assert fbc.db.session.query(SupplyUse).count() == 0


@pytest.mark.parametrize(
    "fields, error",
    [
        ({"on_hand": "lots"}, "Enter whole numbers."),
        ({"on_hand": "-1"}, "can&#39;t be below 0"),
        ({"on_hand": "2"}, "how many uses are left"),
        ({"on_hand": "2", "left": "0"}, "from 1 to 220"),
        ({"on_hand": "2", "left": "221"}, "from 1 to 220"),
        ({"on_hand": "0", "left": "5"}, "blank when none are on hand"),
        ({"left": "5"}, "Enter how many are on hand too."),
    ],
)
def test_reconcile_supplies_refuses_what_the_supply_form_would(client, fields, error):
    mailers, labels, brushes = _reconcile_supplies()
    data = {k + "-" + str(labels.id): v for k, v in fields.items()}

    html = _reconcile(client, action="save", **data)

    assert error in html
    assert "Fix the counts marked below." in html
    # The counts stay filled in to correct.
    for value in fields.values():
        assert 'value="' + value + '"' in html
    fbc.db.session.expire_all()
    assert (labels.quantity, labels.pack_used) == (2, 10)
    assert fbc.db.session.query(SupplyUse).count() == 0


def test_reconcile_supplies_picks_out_the_supply_it_was_linked_for(client):
    mailers, labels, brushes = _reconcile_supplies()

    html = client.get("/reconcilesupplies/?supply=" + str(labels.id)).get_data(
        as_text=True
    )

    assert '<tr id="supply-' + str(labels.id) + '" class="warning">' in html
    assert '<tr id="supply-' + str(mailers.id) + '">' in html


def test_reorder_banner_links_to_reconcile_supplies(client):
    fbc.db.session.add(Supply(name="LP Mailers", quantity=2, min_quantity=5))
    fbc.db.session.commit()

    html = client.get("/").get_data(as_text=True)

    assert "Time to reorder" in html
    assert 'href="/reconcilesupplies/"' in html


def _add_expense(client, **fields):
    data = {
        "date": "2026-03-14",
        "category": "advertising",
        "amount": "12.50",
        "miles": "",
        "mileage_rate": "",
        "vendor": "eBay",
        "note": "Promoted listings",
        "link": "",
    }
    data.update(fields)
    return client.post("/expensemodelview/add", data=data)


def test_expenses_can_be_added_and_listed_by_category_label(client):
    _add_expense(client)

    expense = fbc.db.session.query(Expense).one()
    assert (expense.date, expense.category, expense.amount, expense.vendor) == (
        datetime.date(2026, 3, 14),
        "advertising",
        12.5,
        "eBay",
    )
    html = _menu_list(client, "Expenses")
    assert "Advertising (line 8)" in html and "$12.50" in html
    assert client.get("/expensemodelview/edit/" + str(expense.id)).status_code == 200


def test_expense_miles_work_out_the_amount_at_the_config_rate(client, monkeypatch):
    monkeypatch.setitem(fbc.app.config, "MILEAGE_RATE", 0.725)

    _add_expense(client, category="car", amount="", miles="120")

    expense = fbc.db.session.query(Expense).one()
    assert (expense.amount, expense.miles, expense.mileage_rate) == (87.0, 120, 0.725)


def test_expense_edit_keeps_the_rate_it_was_entered_with(client, monkeypatch):
    monkeypatch.setitem(fbc.app.config, "MILEAGE_RATE", 0.7)
    _add_expense(client, category="car", amount="", miles="10")
    expense = fbc.db.session.query(Expense).one()
    monkeypatch.setitem(fbc.app.config, "MILEAGE_RATE", 0.725)

    client.post(
        "/expensemodelview/edit/" + str(expense.id),
        data={
            "date": "2026-03-14",
            "category": "car",
            "amount": "7.00",
            "miles": "20",
            "mileage_rate": "0.7",
        },
    )

    fbc.db.session.expire_all()
    assert (expense.amount, expense.mileage_rate) == (14.0, 0.7)


@pytest.mark.parametrize(
    "fields, error",
    [
        ({"amount": ""}, "Enter an Amount, or Miles for mileage."),
        ({"amount": "", "miles": "10"}, "MILEAGE_RATE isn't set in config.py"),
        ({"amount": "twelve"}, "Not a valid float value"),
        ({"category": "nonsense"}, "Not a valid choice"),
    ],
)
def test_expense_form_refuses_what_it_cant_save(client, fields, error):
    page = _add_expense(client, **fields)

    assert error in page.get_data(as_text=True)
    assert fbc.db.session.query(Expense).count() == 0


def _order_with_two_supplies(client, price="100.00"):
    """A Uline order for mailers and a label roll, through the form."""
    mailers = Supply(name="LP Mailers", quantity=10)
    labels = Supply(name="Shipping Label Roll", quantity=1)
    fbc.db.session.add_all([mailers, labels])
    fbc.db.session.commit()
    client.post(
        "/purchaseordermodelview/add",
        data=MultiDict(
            [
                ("date", "2026-04-01"),
                ("price", price),
                ("notes", "Uline order"),
                ("link", ""),
                ("supply", str(mailers.id)),
                ("supply", str(labels.id)),
            ]
        ),
    )
    return fbc.db.session.query(PurchaseOrder).one()


def test_a_purchase_order_holds_several_supplies_and_expenses(client):
    order = _order_with_two_supplies(client)
    assert sorted(s.name for s in order.supply) == ["LP Mailers", "Shipping Label Roll"]

    _add_expense(client, category="supplies", amount="30", purchase_order=str(order.id))
    # From the order's page, which fills in the order itself.
    client.post(
        "/expensemodelview/add?_flt_0_purchase_order=" + str(order.id),
        data={"date": "2026-04-01", "category": "equipment", "amount": "12.50"},
    )

    fbc.db.session.expire_all()
    assert sorted(e.amount for e in order.expenses) == [12.5, 30.0]
    assert order.supply_cost() == 57.5
    show = client.get("/purchaseordermodelview/show/" + str(order.id))
    html = show.get_data(as_text=True)
    assert "$57.50" in html and "Equipment (line 13)" in html
    assert "LP Mailers, Shipping Label Roll" in html


def test_expenses_cant_come_to_more_than_their_purchase_orders_price(client):
    order = _order_with_two_supplies(client)
    _add_expense(client, amount="60", purchase_order=str(order.id))

    page = _add_expense(client, amount="40.01", purchase_order=str(order.id))

    assert "would come to $100.01, more than its price of $100.00" in (
        page.get_data(as_text=True)
    )
    assert fbc.db.session.query(Expense).count() == 1


def test_editing_an_expense_on_a_purchase_order_doesnt_count_it_twice(client):
    order = _order_with_two_supplies(client)
    _add_expense(client, amount="60", purchase_order=str(order.id))
    expense = fbc.db.session.query(Expense).one()

    client.post(
        "/expensemodelview/edit/" + str(expense.id),
        data={
            "date": "2026-04-01",
            "category": "supplies",
            "amount": "100",
            "purchase_order": str(order.id),
        },
    )

    fbc.db.session.expire_all()
    assert expense.amount == 100.0


def test_a_purchase_orders_price_cant_go_below_its_expenses(client):
    order = _order_with_two_supplies(client)
    _add_expense(client, amount="60", purchase_order=str(order.id))

    page = client.post(
        "/purchaseordermodelview/edit/" + str(order.id),
        data={"date": "2026-04-01", "price": "50", "notes": "Uline order"},
    )

    assert "The Expenses on it come to $60.00" in page.get_data(as_text=True)
    fbc.db.session.expire_all()
    assert order.price == 100.0


# Search by id (#99)


def _every_model_view():
    from fbc.util import CustomModelView

    return [v for v in fbc.appbuilder.baseviews if isinstance(v, CustomModelView)]


def test_every_model_view_searches_by_id():
    views = _every_model_view()
    assert views
    for view in views:
        pk = view.datamodel.get_pk_name()
        assert pk in view.search_columns, view.__class__.__name__
        assert pk in view._filters.get_search_filters(), view.__class__.__name__


def test_integer_ids_get_the_range_filter_first():
    from fbc.util import FilterIdMatches

    for view in _every_model_view():
        pk = view.datamodel.get_pk_name()
        first = view._filters.get_search_filters()[pk][0]
        if view.datamodel.is_integer(pk):
            assert first is FilterIdMatches, view.__class__.__name__
        else:
            assert first is not FilterIdMatches, view.__class__.__name__


def test_id_filter_parse():
    from fbc.util import FilterIdMatches

    parse = FilterIdMatches.parse
    assert parse("5") == [(5, 5)]
    assert parse("100-200") == [(100, 200)]
    assert parse("200 - 100") == [(100, 200)]
    assert parse("5, 7-9") == [(5, 5), (7, 9)]
    for bad in ("", "a", "1-2-3", "-5", "1,,2", "1.5"):
        assert parse(bad) is None, bad


def test_id_filter_applies():
    from fbc import db
    from fbc.util import FilterIdMatches

    view = _view(PurchaseLotModelView)
    model = view.datamodel.obj
    ids = [r.id for r in db.session.query(model).all()]
    f = FilterIdMatches("id", view.datamodel)
    assert db.session.query(model).count() == len(ids)
    assert f.apply(db.session.query(model), "nonsense").count() == 0
    if ids:
        got = [r.id for r in f.apply(db.session.query(model), f"{min(ids)}-{max(ids)}")]
        assert sorted(got) == sorted(ids)
