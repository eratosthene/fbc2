"""Listing a Unit on Discogs (fbc/discogs_listing.py and its views): Discogs is faked."""

import json

import pytest
import requests
from fake_discogs import FakeDiscogs, api_error, failing

import fbc
from fbc import db, devmode, discogs_client, discogs_listing, syncjobs
from fbc.models.discogs import DiscogsInstance, DiscogsListing
from fbc.models.ebay import EbayDraft, EbayListing
from fbc.models.inventory import StorageBox
from test_discogs_listings import _item
from test_listing import SETTINGS, _carnaval


@pytest.fixture
def fake(monkeypatch):
    return FakeDiscogs().install(monkeypatch)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(fbc.app.config["EBAY_SETTINGS"], "LISTING", dict(SETTINGS))
    sm = fbc.appbuilder.sm
    sm.create_db()
    fbc.appbuilder.add_permissions(update_perms=True)
    sm.add_user("admin", "A", "Dmin", "admin@example.com", sm.find_role("Admin"), "pw")
    c = fbc.app.test_client()
    c.post("/login/", data={"username": "admin", "password": "pw"})
    return c


@pytest.fixture(autouse=True)
def eBay_settings(monkeypatch):
    monkeypatch.setitem(fbc.app.config["EBAY_SETTINGS"], "LISTING", dict(SETTINGS))


def _unit(**kwargs):
    unit = _carnaval(
        storage_box=StorageBox(name="Box 4"),
        discogs_instance=DiscogsInstance(instance_id=99),
        notes="Light ring wear",
        **kwargs,
    )
    return unit


def _form(**changes):
    form = {
        "release_id": "1",
        "condition": "Very Good Plus (VG+)",
        "sleeve_condition": "Very Good Plus (VG+)",
        "price": "12.50",
        "comments": "Nice",
        "allow_offers": "1",
        "weight": "230",
        "location": "Box 4",
    }
    form.update(changes)
    return form


def _url(unit):
    return "/discogslistingmodelview/fromunit/" + str(unit.id)


# --- The mapping and the form's starting values ---


@pytest.mark.parametrize(
    "grading, expected",
    [
        ("M/M", ("Mint (M)", "Mint (M)")),
        ("NM/VG+", ("Near Mint (NM or M-)", "Very Good Plus (VG+)")),
        ("M-/VG", ("Near Mint (NM or M-)", "Very Good (VG)")),
        ("NM or M-/G+", ("Near Mint (NM or M-)", "Good Plus (G+)")),
        ("VG+/G", ("Very Good Plus (VG+)", "Good (G)")),
        ("F/P", ("Fair (F)", "Poor (P)")),
        ("VG+", ("Very Good Plus (VG+)", None)),
        ("EX/VG", (None, "Very Good (VG)")),
        ("", (None, None)),
        (None, (None, None)),
    ],
)
def test_grading_maps_to_discogs_conditions(grading, expected):
    assert discogs_listing.conditions_from_grading(grading) == expected


def test_every_mapped_name_is_one_discogs_accepts():
    for name in discogs_listing.CONDITION_FOR_GRADE.values():
        assert name in discogs_listing.MEDIA_CONDITIONS


def test_form_starts_filled_in_from_the_unit(app):
    unit = _unit()

    values = discogs_listing.defaults(unit)

    assert values == {
        "release_id": 1,
        "condition": "Very Good Plus (VG+)",
        "sleeve_condition": "Very Good Plus (VG+)",
        "price": "5.00",
        "comments": "Light ring wear",
        "allow_offers": True,
        "weight": 454,  # a 12" record's 16 oz, in grams
        "external_id": f"fbc-{unit.id}",
        "location": "Box 4",
    }


def test_allow_offers_follows_the_setting(app, monkeypatch):
    monkeypatch.setitem(fbc.app.config["DISCOGS_SETTINGS"], "ALLOW_OFFERS", False)

    assert discogs_listing.defaults(_unit())["allow_offers"] is False


def test_weight_counts_discs(app):
    from fbc.listing import disc_count

    unit = _unit()
    unit.discogs_release.formats = json.dumps([{"name": "Vinyl", "qty": "2"}])

    assert disc_count(unit) == 2
    assert discogs_listing.weight_grams(unit) == 907


def test_the_form_page_shows_the_starting_values(client, fake):
    unit = _unit()

    html = client.get(_url(unit)).get_data(as_text=True)

    assert f"fbc-{unit.id}" in html
    assert 'value="5.00"' in html and 'value="454"' in html
    assert "Light ring wear" in html and "Box 4" in html
    assert "<option selected>Very Good Plus (VG+)</option>" in html
    assert (
        fake.account.calls_to("create_listing") == []
    )  # showing the form posts nothing


# --- Creating a Draft ---


def test_create_posts_a_draft_and_saves_it(client, fake):
    unit = _unit()

    response = client.post(_url(unit), data=_form())

    ((payload,),) = fake.account.calls_to("create_listing")
    assert payload == {
        "release_id": 1,
        "condition": "Very Good Plus (VG+)",
        "sleeve_condition": "Very Good Plus (VG+)",
        "price": 12.5,
        "comments": "Nice",
        "allow_offers": True,
        "status": "Draft",
        "location": "Box 4",
        "weight": 230,
        "external_id": f"fbc-{unit.id}",
    }
    row = db.session.query(DiscogsListing).one()
    assert (row.listing_id, row.status, row.unit_id) == (7001, "Draft", unit.id)
    assert row.url == "https://www.discogs.com/sell/item/7001"
    assert not row.is_for_sale()
    assert response.headers["Location"].endswith("/discogslistingmodelview/show/7001")


def test_the_draft_page_links_to_discogs_and_offers_make_for_sale(client, fake):
    unit = _unit()
    client.post(_url(unit), data=_form())

    html = client.get("/discogslistingmodelview/show/7001").get_data(as_text=True)

    assert "https://www.discogs.com/sell/item/7001" in html
    assert "add pictures" in html
    assert "Make it For Sale" in html


def test_a_form_with_mistakes_posts_nothing(client, fake):
    unit = _unit()

    html = client.post(
        _url(unit), data=_form(condition="", price="0", weight="x")
    ).get_data(as_text=True)

    assert fake.account.calls_to("create_listing") == []
    assert "Choose a Media Condition." in html
    assert "Price has to be more than 0." in html
    assert "Weight has to be a whole number of grams." in html
    assert db.session.query(DiscogsListing).count() == 0


def test_discogs_refusing_the_post_creates_nothing_locally(client, fake):
    fake.account.fail = failing("create_listing", api_error(404, "Release not found."))
    unit = _unit()

    html = client.post(_url(unit), data=_form()).get_data(as_text=True)

    assert "Discogs said no: " in html and "Release not found." in html
    assert db.session.query(DiscogsListing).count() == 0


def test_a_timeout_then_a_retry_does_not_duplicate(client, fake):
    unit = _unit()
    fake.account.fail = failing(
        "create_listing", requests.exceptions.ReadTimeout("slow")
    )

    html = client.post(_url(unit), data=_form()).get_data(as_text=True)

    assert "may or may not have been made" in html
    assert db.session.query(DiscogsListing).count() == 0
    # It had gone through after all. The retry finds it and posts nothing.
    fake.account.fail = None
    fake.account.calls.clear()
    fake.account.inventory_items = [
        _item(
            555,
            status="Draft",
            external_id=f"fbc-{unit.id}",
            release={"id": 1, "description": "Spyro Gyra - Carnaval"},
        )
    ]

    response = client.post(_url(unit), data=_form())

    assert fake.account.calls_to("create_listing") == []
    row = db.session.query(DiscogsListing).one()
    assert (row.listing_id, row.unit_id) == (555, unit.id)
    assert response.headers["Location"].endswith("/show/555")


def test_a_server_error_is_treated_as_uncertain_too(client, fake):
    fake.account.fail = failing("create_listing", api_error(502, "Bad gateway"))

    html = client.post(_url(_unit()), data=_form()).get_data(as_text=True)

    assert "may or may not have been made" in html


def test_a_listing_for_another_unit_in_the_inventory_isnt_adopted(client, fake):
    unit = _unit()
    fake.account.inventory_items = [_item(556, status="Draft", external_id="fbc-99999")]

    client.post(_url(unit), data=_form())

    assert len(fake.account.calls_to("create_listing")) == 1


def test_a_second_listing_is_refused_for_a_draft_and_a_for_sale_one(client, fake):
    unit = _unit()
    db.session.add(DiscogsListing(listing_id=1, unit_id=unit.id, status="Draft"))
    db.session.commit()

    for status in ("Draft", "For Sale"):
        db.session.get(DiscogsListing, 1).status = status
        db.session.commit()
        response = client.post(_url(unit), data=_form())
        assert response.headers["Location"].endswith("/show/1")

    assert fake.account.calls_to("create_listing") == []


def test_an_ended_or_sold_listing_doesnt_block_a_new_one(client, fake):
    unit = _unit()
    db.session.add(
        DiscogsListing(listing_id=1, unit_id=unit.id, status="Sold", ended=True)
    )
    db.session.commit()

    client.post(_url(unit), data=_form())

    assert len(fake.account.calls_to("create_listing")) == 1


def test_a_unit_without_a_collection_copy_cant_be_listed(client, fake):
    unit = _unit()
    unit.discogs_instance = None
    db.session.commit()

    html = client.post(_url(unit), data=_form()).get_data(as_text=True)

    assert "isn&#39;t in your Discogs collection" in html
    assert fake.account.calls_to("create_listing") == []


def test_a_sold_unit_cant_be_listed(client, fake):
    unit = _unit(sold=True)

    html = client.post(_url(unit), data=_form()).get_data(as_text=True)

    assert "This Unit is sold." in html
    assert fake.account.calls_to("create_listing") == []


def test_an_active_ebay_listing_warns_but_doesnt_block(client, fake):
    unit = _unit()
    unit.ebay_listing = EbayListing(
        item_id="123", title="T", price=5.0, url="https://www.ebay.com/itm/123"
    )
    db.session.commit()

    html = client.get(_url(unit)).get_data(as_text=True)
    client.post(_url(unit), data=_form())

    assert "active eBay listing (123)" in html
    assert "https://www.ebay.com/itm/123" in html
    assert len(fake.account.calls_to("create_listing")) == 1


def test_an_unpublished_ebay_draft_warns_with_a_link(client, fake):
    unit = _unit()
    draft = EbayDraft(unit=unit, title="T", status="draft")
    db.session.add(draft)
    db.session.commit()

    html = client.get(_url(unit)).get_data(as_text=True)

    assert "eBay Draft that hasn&#39;t been published" in html
    assert f"/ebaydraftmodelview/show/{draft.id}" in html


def test_an_ended_ebay_listing_doesnt_warn(client, fake):
    unit = _unit()
    unit.ebay_listing = EbayListing(
        item_id="123", title="T", price=5.0, url="u", ended=True
    )
    db.session.commit()

    assert discogs_listing.ebay_conflicts(unit) == []


# --- Making it For Sale ---


def _draft_on_discogs(fake, unit, listing_id=7001, status="Draft"):
    db.session.add(
        DiscogsListing(listing_id=listing_id, unit_id=unit.id, status=status)
    )
    db.session.commit()
    item = _item(listing_id, status=status, external_id=f"fbc-{unit.id}")
    fake.account.listings[listing_id] = item
    return item


def test_make_for_sale_changes_the_status(client, fake):
    unit = _unit()
    _draft_on_discogs(fake, unit)

    response = client.post("/discogslistingmodelview/makeforsale/7001")

    ((listing_id, payload),) = fake.account.calls_to("update_listing")
    assert listing_id == 7001
    assert payload == {
        "release_id": 555,
        "condition": "Near Mint (NM or M-)",
        "price": 12.5,
        "status": "For Sale",
    }
    row = db.session.get(DiscogsListing, 7001)
    assert row.status == "For Sale" and row.is_for_sale()
    assert response.status_code == 302


def test_make_for_sale_does_nothing_if_discogs_no_longer_has_a_draft(client, fake):
    unit = _unit()
    _draft_on_discogs(fake, unit)
    fake.account.listings[7001]["status"] = "Sold"

    client.post("/discogslistingmodelview/makeforsale/7001")

    assert fake.account.calls_to("update_listing") == []
    row = db.session.get(DiscogsListing, 7001)
    assert row.status == "Sold" and row.ended


def test_make_for_sale_refuses_what_isnt_a_draft(client, fake):
    unit = _unit()
    _draft_on_discogs(fake, unit, status="For Sale")

    client.post("/discogslistingmodelview/makeforsale/7001")

    assert fake.account.calls_to("update_listing") == []


def test_make_for_sale_shows_discogs_refusal_and_keeps_the_draft(client, fake):
    unit = _unit()
    _draft_on_discogs(fake, unit)
    fake.account.fail = failing(
        "update_listing", api_error(422, "You must add a picture.")
    )

    client.post("/discogslistingmodelview/makeforsale/7001")

    assert db.session.get(DiscogsListing, 7001).status == "Draft"


def test_make_for_sale_only_answers_a_post(client, fake):
    _draft_on_discogs(fake, _unit())

    assert client.get("/discogslistingmodelview/makeforsale/7001").status_code == 405


# --- A re-sync reconciles rather than duplicates ---


def test_a_resync_updates_the_saved_draft(client, fake):
    unit = _unit()
    client.post(_url(unit), data=_form())
    assert db.session.query(DiscogsListing).count() == 1
    fake.account.inventory_items = [
        _item(
            7001,
            status="For Sale",
            external_id=f"fbc-{unit.id}",
            price={"value": 12.5, "currency": "USD"},
        )
    ]

    syncjobs.sync_discogs_listings()

    assert db.session.query(DiscogsListing).count() == 1
    row = db.session.get(DiscogsListing, 7001)
    assert (row.status, row.unit_id) == ("For Sale", unit.id)


# --- Links on the Unit ---


def test_unit_link_offers_list_on_discogs_then_the_listing(client, fake):
    unit = _unit()

    assert "List&nbsp;on&nbsp;Discogs" in unit.link_column()
    client.post(_url(unit), data=_form())
    db.session.refresh(unit)

    assert "List&nbsp;on&nbsp;Discogs" not in unit.link_column()
    assert "Discogs:Draft" in unit.link_column()


def test_unit_without_a_collection_copy_has_no_link(app):
    unit = _unit()
    unit.discogs_instance = None

    assert "List&nbsp;on&nbsp;Discogs" not in unit.link_column()


# --- Dev mode ---


def test_dev_mode_posts_nothing(client, fake, monkeypatch):
    monkeypatch.setitem(fbc.app.config, "DEV_MODE", True)
    unit = _unit()
    db.session.add(DiscogsListing(listing_id=7001, status="Draft"))
    db.session.commit()

    page = client.get(_url(unit)).get_data(as_text=True)
    posted = client.post(_url(unit), data=_form()).get_data(as_text=True)
    sale = client.post("/discogslistingmodelview/makeforsale/7001")

    assert "Dev Mode is turned on, listing on Discogs disabled." in page
    assert "Dev Mode is turned on" in posted
    assert sale.status_code == 302
    assert db.session.query(DiscogsListing).count() == 1  # only the one set up
    assert fake.account.calls == []  # not even a look


def test_the_write_functions_refuse_in_dev_mode_even_if_called_directly(
    app, monkeypatch
):
    monkeypatch.setitem(fbc.app.config, "DEV_MODE", True)
    unit = _unit()
    row = DiscogsListing(listing_id=1, status="Draft", unit_id=unit.id)

    with pytest.raises(discogs_client.DiscogsAuthError):
        discogs_listing.create_listing(unit, {})
    with pytest.raises(discogs_client.DiscogsAuthError):
        discogs_listing.make_for_sale(row)
    assert devmode.on()
