import io
import json

import pytest

import fbc
from fbc import db
from fbc.ebay import api, drafts
from fbc.models.ebay import EbayDraft
from fake_pictures import give_pictures, jpeg
from fbc.pictures import store
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


def test_uploading_pictures_keeps_them_on_the_unit_and_never_calls_ebay(
    client, monkeypatch
):
    monkeypatch.setattr(
        "fbc.ebay.pictures.upload_picture",
        lambda *a, **k: pytest.fail("pictures aren't sent to eBay when added"),
    )
    unit = _carnaval()
    client.get("/ebaydraftmodelview/fromunit/" + str(unit.id))
    draft = db.session.query(EbayDraft).one()

    client.post(
        "/ebaydraftmodelview/pictures/" + str(draft.id),
        data={
            "pictures": [
                (io.BytesIO(jpeg(40, 20, color="red")), "front.jpg"),
                (io.BytesIO(jpeg(40, 20, color="blue")), "back.jpg"),
            ]
        },
        content_type="multipart/form-data",
    )
    client.post("/ebaydraftmodelview/picture/" + str(draft.id) + "/1/first")
    db.session.expire_all()

    assert [p.upload_name for p in draft.stored_pictures()] == ["back.jpg", "front.jpg"]
    assert [p.position for p in draft.stored_pictures()] == [1, 2]
    assert draft.ebay_picture_urls() == []
    page = client.get("/ebaydraftmodelview/show/" + str(draft.id))
    assert "disabled>Publish to eBay" not in page.get_data(as_text=True)


def test_draft_page_shows_the_stored_pictures_and_serves_them(client):
    unit = _carnaval()
    client.get("/ebaydraftmodelview/fromunit/" + str(unit.id))
    draft = db.session.query(EbayDraft).one()
    give_pictures(draft, ["https://i.ebayimg.com/a.jpg"])

    html = client.get("/ebaydraftmodelview/show/" + str(draft.id)).get_data(
        as_text=True
    )
    served = client.get("/ebaydraftmodelview/picturefile/%d/0" % draft.id)

    assert "/ebaydraftmodelview/picturefile/%d/0" % draft.id in html
    assert served.status_code == 200
    assert served.mimetype == "image/jpeg"
    assert served.data == store.path(draft.stored_pictures()[0]).read_bytes()
    assert (
        client.get("/ebaydraftmodelview/picturefile/%d/5" % draft.id).status_code == 404
    )


def test_removing_a_picture_deletes_its_files(client):
    unit = _carnaval()
    client.get("/ebaydraftmodelview/fromunit/" + str(unit.id))
    draft = db.session.query(EbayDraft).one()
    first, second = give_pictures(
        draft, ["https://i.ebayimg.com/a.jpg", "https://i.ebayimg.com/b.jpg"]
    )
    gone = [store.path(first), store.original_path(first)]

    client.post("/ebaydraftmodelview/picture/%d/0/remove" % draft.id)
    db.session.expire_all()

    assert not any(p.exists() for p in gone)
    assert draft.ebay_picture_urls() == ["https://i.ebayimg.com/b.jpg"]
    assert [p.position for p in draft.stored_pictures()] == [1]


def test_picture_refuses_an_unknown_action_or_index(client):
    unit = _carnaval()
    client.get("/ebaydraftmodelview/fromunit/" + str(unit.id))
    draft = db.session.query(EbayDraft).one()
    give_pictures(draft, ["https://i.ebayimg.com/a.jpg"])

    assert (
        client.post("/ebaydraftmodelview/picture/%d/0/burn" % draft.id).status_code
        == 400
    )
    assert (
        client.post("/ebaydraftmodelview/picture/%d/3/first" % draft.id).status_code
        == 404
    )
    assert draft.picture_count() == 1


def test_a_file_that_isnt_a_picture_is_refused_and_the_rest_are_kept(client):
    unit = _carnaval()
    client.get("/ebaydraftmodelview/fromunit/" + str(unit.id))
    draft = db.session.query(EbayDraft).one()

    page = client.post(
        "/ebaydraftmodelview/pictures/" + str(draft.id),
        data={
            "pictures": [(io.BytesIO(b"junk"), "x.jpg"), (io.BytesIO(jpeg()), "ok.jpg")]
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert "x.jpg: Couldn&#39;t read the picture" in page.get_data(as_text=True)
    assert [p.upload_name for p in draft.stored_pictures()] == ["ok.jpg"]


def test_no_more_than_24_pictures(client):
    unit = _carnaval()
    client.get("/ebaydraftmodelview/fromunit/" + str(unit.id))
    draft = db.session.query(EbayDraft).one()
    give_pictures(draft, ["https://i.ebayimg.com/p%d.jpg" % n for n in range(24)])

    page = client.post(
        "/ebaydraftmodelview/pictures/" + str(draft.id),
        data={"pictures": [(io.BytesIO(jpeg()), "extra.jpg")]},
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert "eBay allows at most 24 pictures." in page.get_data(as_text=True)
    assert draft.picture_count() == 24


def test_without_a_folder_pictures_cant_be_added(client, app, monkeypatch):
    monkeypatch.setitem(app.config, "PICTURES_DIR", None)
    unit = _carnaval()
    client.get("/ebaydraftmodelview/fromunit/" + str(unit.id))
    draft = db.session.query(EbayDraft).one()

    html = client.get("/ebaydraftmodelview/show/" + str(draft.id)).get_data(
        as_text=True
    )
    page = client.post(
        "/ebaydraftmodelview/pictures/" + str(draft.id),
        data={"pictures": [(io.BytesIO(jpeg()), "a.jpg")]},
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert "PICTURES_DIR isn&#39;t set" in html or "PICTURES_DIR isn't set" in html
    assert "Set PICTURES_DIR in config.py" in page.get_data(as_text=True)
    assert draft.picture_count() == 0


def test_publish_endpoint_lists_the_draft(client, monkeypatch):
    def fake_call(verb, data):
        resp = {"ItemID": "123", "StartTime": "2026-10-29T16:00:00.000Z"}
        return resp, api.summarize_response(resp)

    monkeypatch.setattr(api, "_call", fake_call)
    unit = _carnaval()
    client.get("/ebaydraftmodelview/fromunit/" + str(unit.id))
    draft = db.session.query(EbayDraft).one()
    give_pictures(draft, ["https://i.ebayimg.com/a.jpg"])

    client.post("/ebaydraftmodelview/publish/" + str(draft.id))
    db.session.expire_all()

    assert draft.status == "published"
    assert draft.unit.ebay_listing_id == "123"
    assert "List&nbsp;on&nbsp;eBay" not in str(draft.unit.link_column())


def _no_account_profiles():
    return (
        {"shipping": [], "return": [], "payment": []},
        {"errors": [], "warnings": [], "fees": []},
        {
            "ack": "Success",
            "returned_preferences": False,
            "opted_in": None,
            "profile_count": 0,
        },
    )


def test_policies_page_lists_profile_ids(client, monkeypatch):
    monkeypatch.setattr(
        "fbc.views.listing.seller_profiles",
        lambda: (
            {"shipping": [("111", "Media Mail")], "return": [], "payment": []},
            {"errors": [], "warnings": [], "fees": []},
            {
                "ack": "Success",
                "returned_preferences": True,
                "opted_in": "true",
                "profile_count": 1,
            },
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
            {
                "shipping": ("111", "Media Mail"),
                "return": ("222", "No returns"),
                "payment": None,
            },
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
            drafts.template_from_item(FLAT_ITEM),
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

    monkeypatch.setattr("fbc.views.connect.exchange_code", fake_exchange)
    monkeypatch.setitem(fbc.app.config["EBAY_SETTINGS"], "RUNAME", "the-runame")
    return codes


def _state(client):
    with client.session_transaction() as s:
        return s["ebay_oauth_state"]


def test_connect_page_links_to_ebay_sign_in(client, exchanged):
    html = client.get("/connectebay/").get_data(as_text=True)

    assert "Not connected yet." in html
    assert "https://auth.ebay.com/oauth2/authorize?" in html
    assert "state=" + _state(client) in html


def test_pasted_address_is_exchanged(client, exchanged):
    client.get("/connectebay/")
    address = (
        "https://signin.ebay.com/ws/eBayISAPI.dll?ThirdPartyAuthSucessFailure"
        "&state=" + _state(client) + "&code=v%5E1.1%23i%5E1&expires_in=299"
    )

    page = client.post("/connectebay/", data={"address": address})

    assert page.status_code == 302
    assert exchanged == ["v^1.1#i^1"]


def test_ebay_redirecting_straight_back_is_exchanged(client, exchanged):
    client.get("/connectebay/")

    client.get("/connectebay/?state=" + _state(client) + "&code=abc&expires_in=299")

    assert exchanged == ["abc"]


def test_connect_ebay_is_in_the_sales_menu(client, exchanged):
    html = client.get("/ebaydraftmodelview/list/").get_data(as_text=True)

    assert 'href="/connectebay/"' in html
    assert html.count("Connect eBay") == 1


def test_the_old_connect_address_still_works(client, exchanged):
    client.get("/connectebay/")
    query = "state=" + _state(client) + "&code=abc&expires_in=299"

    page = client.get("/ebaydraftmodelview/oauth?" + query)

    assert page.status_code == 307
    assert page.headers["Location"].endswith("/connectebay/?" + query)
    client.get(page.headers["Location"])
    assert exchanged == ["abc"]


def test_sign_in_from_another_page_is_refused(client, exchanged):
    client.get("/connectebay/")

    page = client.post(
        "/connectebay/",
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
            created=datetime.datetime(
                2026, 10, 29, 12
            ),  # noon UTC, the same day in Central
            expires=datetime.datetime(2028, 4, 28, 12),
        )
    )
    db.session.commit()

    html = client.get("/connectebay/").get_data(as_text=True)
    assert "Connected 2026-10-29; sign in again before 2028-04-28." in html


def _draft_with_result(result):
    unit = _carnaval()
    from fbc.ebay.drafts import new_draft

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
    html = client.get("/ebaydraftmodelview/show/" + str(draft.id)).get_data(
        as_text=True
    )

    assert (
        "These are eBay&#39;s estimate." in html or "These are eBay's estimate." in html
    )
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
    html = client.get("/ebaydraftmodelview/show/" + str(draft.id)).get_data(
        as_text=True
    )

    assert "InsertionFee" in html
    assert "$0.35" in html


def _size(path):
    from PIL import Image

    with Image.open(path) as image:
        return image.size


@pytest.fixture
def rotating(client, monkeypatch):
    """A draft with three stored pictures that eBay has, and eBay calls that fail."""
    monkeypatch.setattr(
        "fbc.ebay.pictures.upload_picture",
        lambda *a, **k: pytest.fail("rotating doesn't call eBay"),
    )
    unit = _carnaval()
    client.get("/ebaydraftmodelview/fromunit/" + str(unit.id))
    draft = db.session.query(EbayDraft).one()
    give_pictures(draft, ["https://i.ebayimg.com/" + n + ".jpg" for n in "abc"])
    return draft


@pytest.mark.parametrize("direction", ["left", "right"])
def test_rotating_a_picture_turns_the_stored_files_and_keeps_its_place(
    client, rotating, direction
):
    draft = rotating
    before = draft.stored_pictures()[1]
    old = store.path(before)

    client.post("/ebaydraftmodelview/rotate/%d/1/%s" % (draft.id, direction))
    db.session.expire_all()

    pictures = draft.stored_pictures()
    assert [p.position for p in pictures] == [1, 2, 3]
    assert pictures[1].id == before.id
    assert _size(store.path(pictures[1])) == (20, 40)
    assert _size(store.original_path(pictures[1])) == (20, 40)
    assert _size(store.path(pictures[0])) == (40, 20)
    assert not old.exists()
    # Only the turned one has to be sent to eBay again.
    assert draft.ebay_picture_urls() == [
        "https://i.ebayimg.com/a.jpg",
        "https://i.ebayimg.com/c.jpg",
    ]


def test_rotating_left_and_right_go_opposite_ways():
    from PIL import Image

    from fbc.ebay.pictures import rotate_picture

    image = Image.new("RGB", (4, 2), "white")
    image.putpixel((0, 0), (255, 0, 0))
    out = io.BytesIO()
    image.save(out, format="PNG")

    left = Image.open(rotate_picture(out.getvalue(), "left")[0])
    right = Image.open(rotate_picture(out.getvalue(), "right")[0])

    assert left.getpixel((0, 3)) == (255, 0, 0)
    assert right.getpixel((1, 0)) == (255, 0, 0)


@pytest.mark.parametrize(
    "path, status",
    [("9/left", 404), ("-1/left", 404), ("0/up", 400)],
)
def test_rotate_refuses_a_bad_index_or_direction(client, rotating, path, status):
    draft = rotating

    r = client.post("/ebaydraftmodelview/rotate/%d/%s" % (draft.id, path))

    assert r.status_code == status
    assert _size(store.path(draft.stored_pictures()[0])) == (40, 20)


def test_rotate_refuses_a_missing_or_published_draft(client, rotating):
    draft = rotating
    assert client.post("/ebaydraftmodelview/rotate/999/0/left").status_code == 404
    draft.status = "published"
    db.session.commit()

    r = client.post("/ebaydraftmodelview/rotate/%d/0/left" % draft.id)

    assert r.status_code == 404
    assert _size(store.path(draft.stored_pictures()[0])) == (40, 20)


def test_a_file_that_cant_be_read_leaves_the_pictures_alone(client, rotating):
    draft = rotating
    picture = draft.stored_pictures()[0]
    store.path(picture).write_bytes(b"junk")

    page = client.post(
        "/ebaydraftmodelview/rotate/%d/0/left" % draft.id, follow_redirects=True
    )

    assert "Couldn&#39;t read the picture" in page.get_data(as_text=True)
    db.session.expire_all()
    assert draft.stored_pictures()[0].ebay_url == "https://i.ebayimg.com/a.jpg"


def test_new_uploads_are_straightened_by_their_exif_tag(client):
    unit = _carnaval()
    client.get("/ebaydraftmodelview/fromunit/" + str(unit.id))
    draft = db.session.query(EbayDraft).one()

    client.post(
        "/ebaydraftmodelview/pictures/" + str(draft.id),
        data={
            "pictures": [
                (io.BytesIO(jpeg(40, 20, orientation=6)), "sideways.jpg"),
                (io.BytesIO(jpeg(40, 20)), "plain.jpg"),
            ]
        },
        content_type="multipart/form-data",
    )

    sideways, plain = draft.stored_pictures()
    assert _size(store.path(sideways)) == (20, 40)
    assert _size(store.path(plain)) == (40, 20)


def test_draft_page_has_the_rotate_before_upload_preview(client):
    unit = _carnaval()
    client.get("/ebaydraftmodelview/fromunit/" + str(unit.id))
    draft = db.session.query(EbayDraft).one()

    html = client.get("/ebaydraftmodelview/show/" + str(draft.id)).get_data(
        as_text=True
    )

    assert 'id="picture-previews"' in html
    assert "Rotate left" in html
