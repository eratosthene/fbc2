import io
import json

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

    def fake_upload(fileobj, filename, content_type=None):
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


def _add_listings():
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


def test_policies_page_shows_policy_ids_off_the_newest_listing(client, monkeypatch):
    _add_listings()
    asked = []

    def fake_listing_details(item_id):
        asked.append(item_id)
        return (
            {"shipping": ("111", "Media Mail"), "return": ("222", "No returns"), "payment": None},
            {},
            {"errors": [], "warnings": [], "fees": []},
        )

    monkeypatch.setattr("fbc.views.listing.seller_profiles", _no_account_profiles)
    monkeypatch.setattr("fbc.views.listing.listing_details", fake_listing_details)

    html = client.get("/ebaydraftmodelview/policies").get_data(as_text=True)
    assert asked == ["NEW"]
    assert "uses business policies" in html
    assert "No returns" in html
    assert "Not set on this listing" in html
    assert "sets shipping and returns on each listing" in html

    client.get("/ebaydraftmodelview/policies?item_id=397798127145")
    assert asked == ["NEW", "397798127145"]


def test_policies_page_shows_what_a_listing_without_policies_would_copy(
    client, monkeypatch
):
    from test_listing import FLAT_ITEM

    _add_listings()
    monkeypatch.setattr("fbc.views.listing.seller_profiles", _no_account_profiles)
    monkeypatch.setattr(
        "fbc.views.listing.listing_details",
        lambda item_id: (
            {"shipping": None, "return": None, "payment": None},
            listing.template_from_item(FLAT_ITEM),
            {"errors": [], "warnings": [], "fees": []},
        ),
    )

    html = client.get("/ebaydraftmodelview/policies").get_data(as_text=True)
    assert "'TEMPLATE_ITEM_ID': 'NEW'" in html
    assert "USPSMedia, $4.39" in html

    monkeypatch.setitem(
        fbc.app.config["EBAY_SETTINGS"]["LISTING"], "TEMPLATE_ITEM_ID", "OLD"
    )
    html = client.get("/ebaydraftmodelview/policies").get_data(as_text=True)
    assert "New listings copy this from it" in html
    assert "ebay.com/itm/OLD" in html


@pytest.fixture
def exchanged(monkeypatch):
    codes = []

    def fake_exchange(code):
        codes.append(code)

    monkeypatch.setattr("fbc.views.listing.exchange_code", fake_exchange)
    monkeypatch.setitem(fbc.app.config["EBAY_SETTINGS"], "RUNAME", "the-runame")
    return codes


def _state(client):
    with client.session_transaction() as s:
        return s["ebay_oauth_state"]


def test_connect_page_links_to_ebay_sign_in(client, exchanged):
    html = client.get("/ebaydraftmodelview/oauth").get_data(as_text=True)

    assert "Not connected yet." in html
    assert "https://auth.ebay.com/oauth2/authorize?" in html
    assert "state=" + _state(client) in html


def test_pasted_address_is_exchanged(client, exchanged):
    client.get("/ebaydraftmodelview/oauth")
    address = (
        "https://signin.ebay.com/ws/eBayISAPI.dll?ThirdPartyAuthSucessFailure"
        "&state=" + _state(client) + "&code=v%5E1.1%23i%5E1&expires_in=299"
    )

    page = client.post("/ebaydraftmodelview/oauth", data={"address": address})

    assert page.status_code == 302
    assert exchanged == ["v^1.1#i^1"]


def test_ebay_redirecting_straight_back_is_exchanged(client, exchanged):
    client.get("/ebaydraftmodelview/oauth")

    client.get(
        "/ebaydraftmodelview/oauth?state=" + _state(client) + "&code=abc&expires_in=299"
    )

    assert exchanged == ["abc"]


def test_sign_in_from_another_page_is_refused(client, exchanged):
    client.get("/ebaydraftmodelview/oauth")

    page = client.post(
        "/ebaydraftmodelview/oauth",
        data={"address": "https://x/?state=forged&code=abc"},
        follow_redirects=True,
    )

    assert exchanged == []
    assert "started from a different page" in page.get_data(as_text=True)


def test_connect_page_says_when_connected(client, exchanged):
    import datetime

    from fbc.models.ebay import EbayOAuthToken

    db.session.add(
        EbayOAuthToken(
            refresh_token="r",
            created=datetime.datetime(2026, 10, 29),
            expires=datetime.datetime(2028, 4, 28),
        )
    )
    db.session.commit()

    html = client.get("/ebaydraftmodelview/oauth").get_data(as_text=True)
    assert "Connected 2026-10-29; sign in again before 2028-04-28." in html


def _draft_with_result(result):
    unit = _carnaval()
    from fbc.listing import new_draft

    draft = new_draft(unit)
    draft.messages = json.dumps(result)
    db.session.add(draft)
    db.session.commit()
    return draft


def test_checked_fees_are_shown_as_an_estimate_with_discounts(client):
    draft = _draft_with_result(
        {
            "errors": [],
            "warnings": [],
            "source": "verify",
            "fees": [
                {
                    "name": "InsertionFee",
                    "label": "Insertion fee",
                    "amount": 0.35,
                    "discount": 0.35,
                    "net": 0.0,
                }
            ],
            "fee_total": {"amount": 0.35, "discount": 0.35, "net": 0.0},
        }
    )
    html = client.get("/ebaydraftmodelview/show/" + str(draft.id)).get_data(as_text=True)

    assert "These are eBay&#39;s estimate." in html or "These are eBay's estimate." in html
    assert "Insertion fee" in html
    assert "&minus;$0.35" in html
    assert "<th>Total</th>" in html


def test_fees_saved_before_discounts_still_show(client):
    draft = _draft_with_result(
        {
            "errors": [],
            "warnings": [],
            "fees": [{"name": "InsertionFee", "amount": 0.35}],
        }
    )
    html = client.get("/ebaydraftmodelview/show/" + str(draft.id)).get_data(as_text=True)

    assert "InsertionFee" in html
    assert "$0.35" in html
