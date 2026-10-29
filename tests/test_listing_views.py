import io

import pytest

import fbc
from fbc import db
from fbc import listing
from fbc.models.ebay import EbayDraft
from test_listing import SETTINGS, _carnaval


@pytest.fixture
def client(monkeypatch):
    """A test client logged in as an Admin, with listing settings in place."""
    monkeypatch.setitem(fbc.app.config["EBAY_SETTINGS"], "LISTING", dict(SETTINGS))
    sm = fbc.appbuilder.sm
    sm.create_db()
    fbc.appbuilder.add_permissions(update_perms=True)
    sm.add_user("admin", "A", "Dmin", "admin@example.com", sm.find_role("Admin"), "pw")
    client = fbc.app.test_client()
    client.post("/login/", data={"username": "admin", "password": "pw"})
    return client


def test_list_on_ebay_creates_one_draft_per_unit(client):
    unit = _carnaval()

    first = client.get("/ebaydraftmodelview/fromunit/" + str(unit.id))
    second = client.get("/ebaydraftmodelview/fromunit/" + str(unit.id))

    assert first.status_code == 302
    assert first.headers["Location"] == second.headers["Location"]
    assert db.session.query(EbayDraft).count() == 1


def test_draft_page_shows_specifics_and_blocks_publish_without_pictures(client):
    unit = _carnaval()
    client.get("/ebaydraftmodelview/fromunit/" + str(unit.id))
    draft = db.session.query(EbayDraft).one()

    page = client.get("/ebaydraftmodelview/show/" + str(draft.id))
    html = page.get_data(as_text=True)

    assert page.status_code == 200
    assert "Record Grading: Very Good Plus (VG+)" in html
    assert "Add at least one picture before publishing." in html
    assert "disabled>Publish to eBay" in html


def test_uploading_pictures_adds_them_in_order(client, monkeypatch):
    uploaded = []

    def fake_upload(fileobj, filename):
        uploaded.append(filename)
        return "https://i.ebayimg.com/" + filename, {
            "errors": [],
            "warnings": [],
            "fees": [],
        }

    monkeypatch.setattr("fbc.views.listing.upload_picture", fake_upload)
    unit = _carnaval()
    client.get("/ebaydraftmodelview/fromunit/" + str(unit.id))
    draft = db.session.query(EbayDraft).one()

    client.post(
        "/ebaydraftmodelview/pictures/" + str(draft.id),
        data={
            "pictures": [
                (io.BytesIO(b"front"), "front.jpg"),
                (io.BytesIO(b"back"), "back.jpg"),
            ]
        },
        content_type="multipart/form-data",
    )
    client.post("/ebaydraftmodelview/picture/" + str(draft.id) + "/1/first")
    db.session.expire_all()

    assert uploaded == ["front.jpg", "back.jpg"]
    assert draft.picture_list() == [
        "https://i.ebayimg.com/back.jpg",
        "https://i.ebayimg.com/front.jpg",
    ]
    page = client.get("/ebaydraftmodelview/show/" + str(draft.id))
    assert "disabled>Publish to eBay" not in page.get_data(as_text=True)


def test_publish_endpoint_lists_the_draft(client, monkeypatch):
    def fake_call(verb, data):
        resp = {"ItemID": "123", "StartTime": "2026-10-29T16:00:00.000Z"}
        return resp, listing.summarize_response(resp)

    monkeypatch.setattr(listing, "_call", fake_call)
    unit = _carnaval()
    client.get("/ebaydraftmodelview/fromunit/" + str(unit.id))
    draft = db.session.query(EbayDraft).one()
    draft.set_picture_list(["https://i.ebayimg.com/a.jpg"])
    db.session.commit()

    client.post("/ebaydraftmodelview/publish/" + str(draft.id))
    db.session.expire_all()

    assert draft.status == "published"
    assert draft.unit.ebay_listing_id == "123"
    assert "List&nbsp;on&nbsp;eBay" not in str(draft.unit.link_column())


def _no_account_profiles():
    return (
        {"shipping": [], "return": [], "payment": []},
        {"errors": [], "warnings": [], "fees": []},
        {"ack": "Success", "returned_preferences": False, "opted_in": None, "profile_count": 0},
    )


def test_policies_page_lists_profile_ids(client, monkeypatch):
    monkeypatch.setattr(
        "fbc.views.listing.seller_profiles",
        lambda: (
            {"shipping": [("111", "Media Mail")], "return": [], "payment": []},
            {"errors": [], "warnings": [], "fees": []},
            {"ack": "Success", "returned_preferences": True, "opted_in": "true", "profile_count": 1},
        ),
    )
    html = client.get("/ebaydraftmodelview/policies").get_data(as_text=True)
    assert "Media Mail" in html
    assert "111" in html


def test_policies_page_reads_ids_off_the_newest_listing(client, monkeypatch):
    import datetime

    from fbc.models.ebay import EbayListing

    for item_id, day in (("OLD", 1), ("NEW", 2)):
        db.session.add(
            EbayListing(
                item_id=item_id,
                title="t",
                price=1.0,
                url="u",
                start_time=datetime.datetime(2026, 10, day),
            )
        )
    db.session.commit()
    asked = []

    def fake_listing_profiles(item_id):
        asked.append(item_id)
        return (
            {"shipping": ("111", "Media Mail"), "return": ("222", "No returns"), "payment": None},
            {"errors": [], "warnings": [], "fees": []},
        )

    monkeypatch.setattr("fbc.views.listing.seller_profiles", _no_account_profiles)
    monkeypatch.setattr("fbc.views.listing.listing_profiles", fake_listing_profiles)

    html = client.get("/ebaydraftmodelview/policies").get_data(as_text=True)
    assert asked == ["NEW"]
    assert "Used by listing" in html
    assert "No returns" in html
    assert "Not set on this listing" in html
    assert "listed no policies" in html

    client.get("/ebaydraftmodelview/policies?item_id=397798127145")
    assert asked == ["NEW", "397798127145"]
