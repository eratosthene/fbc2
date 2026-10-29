import datetime
import json

import pytest

import fbc
from fbc import db
from fbc import listing
from fbc.listing import (
    build_item,
    build_item_specifics,
    build_title,
    check_ready,
    format_specifics,
    new_draft,
    parse_specifics,
    publish_draft,
    split_grading,
    summarize_response,
)
from fbc.models.discogs import Artist, DiscogsRelease, Genre, Style
from fbc.models.ebay import EbayDraft, EbayListing
from fbc.models.inventory import PurchaseLot, Unit
from fbc.util import add_ebay_listing

SETTINGS = {
    "SHIPPING_PROFILE_ID": "111",
    "RETURN_PROFILE_ID": "222",
    "PAYMENT_PROFILE_ID": "333",
    "POSTAL_CODE": "78626",
}


@pytest.fixture(autouse=True)
def listing_settings(monkeypatch):
    monkeypatch.setitem(fbc.app.config["EBAY_SETTINGS"], "LISTING", dict(SETTINGS))


def _carnaval(**unit_kwargs):
    """The Unit behind https://www.ebay.com/itm/397798127145."""
    release = DiscogsRelease(
        release_id=1,
        title="Carnaval",
        year=1980,
        master_id=5,
        formats=json.dumps(
            [
                {
                    "name": "Vinyl",
                    "qty": "1",
                    "descriptions": ["LP", "Album", "Stereo"],
                }
            ]
        ),
        labels=json.dumps([{"name": "MCA Records", "catno": "MCA-5149"}]),
        country="US",
        released="1980",
        notes="[]",
    )
    release.artists = [Artist(artist_id=1, name="Spyro Gyra")]
    release.genres = [Genre(name="Jazz"), Genre(name="Funk / Soul")]
    release.styles = [Style(name="Fusion"), Style(name="Jazz Funk")]
    lot = PurchaseLot(name="Lot", date=datetime.date(2024, 1, 1), price=10.0)
    fields = dict(
        name="Spyro Gyra - Carnaval (1980)",
        unit_type='12" LP',
        grading="VG+/VG+",
        pressing="First Edition",
        matrix="MCA-2395-MD3 MASTERDISK RL",
        description="Cover looks great, very little wear.",
        retail_price=5.0,
        sold=False,
    )
    fields.update(unit_kwargs)
    unit = Unit(discogs_release=release, purchase_lot=lot, **fields)
    db.session.add(unit)
    db.session.commit()
    return unit


def test_item_specifics_match_the_example_listing(app):
    specifics = build_item_specifics(_carnaval())

    assert specifics == {
        "Artist": ["Spyro Gyra"],
        "Release Title": ["Carnaval"],
        "Record Label": ["MCA Records"],
        "Catalog Number": ["MCA-5149"],
        "MPN": ["MCA-5149"],
        "Release Year": ["1980"],
        "Era": ["1980s"],
        "Genre": ["Jazz", "Funk", "Soul"],
        "Style": ["1980s", "Fusion", "Jazz Funk"],
        "Country of Origin": ["United States"],
        "Type": ["LP"],
        "Record Size": ['12"'],
        "Speed": ["33 RPM"],
        "Number of Audio Channels": ["Stereo"],
        "Vinyl Matrix Number": ["MCA-2395-MD3 MASTERDISK RL"],
        "Edition": ["First Edition"],
        "Record Grading": ["Very Good Plus (VG+)"],
        "Sleeve Grading": ["Very Good Plus (VG+)"],
        "Inlay Condition": ["Very Good Plus (VG+)"],
        "Format": ["Record"],
        "Material": ["Vinyl"],
        "Case Type": ["Cardboard Sleeve"],
        "Color": ["Black"],
    }


def test_item_specifics_read_size_speed_and_color_from_discogs_formats(app):
    unit = _carnaval(unit_type="Single")
    unit.discogs_release.formats = json.dumps(
        [
            {
                "name": "Vinyl",
                "descriptions": ['7"', "45 RPM", "Single", "Mono"],
                "text": "Red Translucent",
            }
        ]
    )
    specifics = build_item_specifics(unit)

    assert specifics["Record Size"] == ['7"']
    assert specifics["Speed"] == ["45 RPM"]
    assert specifics["Type"] == ["Single"]
    assert specifics["Number of Audio Channels"] == ["Mono"]
    assert specifics["Color"] == ["Red"]


def test_item_specifics_strip_discogs_disambiguation_and_skip_missing_catno(app):
    unit = _carnaval()
    unit.discogs_release.artists[0].name = "Nirvana (2)"
    unit.discogs_release.labels = json.dumps([{"name": "Pye (3)", "catno": "none"}])
    specifics = build_item_specifics(unit)

    assert specifics["Artist"] == ["Nirvana"]
    assert specifics["Record Label"] == ["Pye"]
    assert "Catalog Number" not in specifics
    assert "MPN" not in specifics


def test_item_specifics_without_discogs_release_use_unit_fields(app):
    unit = Unit(name="Mystery", unit_type='12" LP', grading="VG", pressing="")
    specifics = build_item_specifics(unit, defaults={})

    assert specifics == {
        "Type": ["LP"],
        "Record Size": ['12"'],
        "Speed": ["33 RPM"],
        "Record Grading": ["Very Good (VG)"],
    }


@pytest.mark.parametrize(
    "grading,expected",
    [
        ("VG+/VG+", ("VG+", "VG+")),
        ("NM or M-/VG", ("NM or M-", "VG")),
        ("VG+", ("VG+", None)),
        ("", (None, None)),
        (None, (None, None)),
    ],
)
def test_split_grading(grading, expected):
    assert split_grading(grading) == expected


def test_specifics_text_round_trips():
    specifics = {"Genre": ["Jazz", "Funk"], "Artist": ["Spyro Gyra"]}
    text = format_specifics(specifics)

    assert text == "Genre: Jazz | Funk\nArtist: Spyro Gyra"
    assert parse_specifics(text) == specifics


def test_parse_specifics_skips_blank_and_malformed_lines():
    text = "Artist: Spyro Gyra\n\nnot a specific\nColor:  \nType: LP"
    assert parse_specifics(text) == {"Artist": ["Spyro Gyra"], "Type": ["LP"]}


def test_title_in_house_style(app):
    assert (
        build_title(_carnaval())
        == "Spyro Gyra - Carnaval 1980 First Edition Vinyl LP Fusion Jazz-Funk VG+"
    )


def test_title_drops_styles_to_fit_80_characters(app):
    unit = _carnaval()
    unit.discogs_release.title = "A Considerably Longer Album Title Than Usual"
    title = build_title(unit)

    assert len(title) <= 80
    assert title.endswith("Vinyl LP VG+")
    assert "Fusion" not in title


def test_new_draft_is_filled_in_from_the_unit(app):
    unit = _carnaval()
    draft = new_draft(unit)

    assert draft.title.startswith("Spyro Gyra - Carnaval 1980")
    assert draft.price == 5.0
    assert draft.best_offer is True
    assert draft.description == "<p>Cover looks great, very little wear.</p>"
    assert "Artist: Spyro Gyra" in draft.item_specifics
    assert draft.status == "draft"


def _draft(pictures=None, **kwargs):
    unit = _carnaval()
    draft = new_draft(unit)
    for k, v in kwargs.items():
        setattr(draft, k, v)
    draft.set_picture_list(pictures or [])
    db.session.add(draft)
    db.session.commit()
    return draft


def test_build_item_has_everything_add_fixed_price_item_needs(app):
    draft = _draft(pictures=["https://i.ebayimg.com/a.jpg"])
    item = build_item(draft, SETTINGS)

    assert item["PrimaryCategory"] == {"CategoryID": "176985"}
    assert item["StartPrice"] == "5.00"
    assert item["ConditionID"] == 3000
    assert item["ListingType"] == "FixedPriceItem"
    assert item["SKU"] == "fbc-" + str(draft.unit_id)
    assert item["PictureDetails"] == {"PictureURL": ["https://i.ebayimg.com/a.jpg"]}
    assert item["BestOfferDetails"] == {"BestOfferEnabled": "true"}
    assert item["PostalCode"] == "78626"
    assert item["SellerProfiles"]["SellerShippingProfile"] == {
        "ShippingProfileID": "111"
    }
    assert {"Name": "Artist", "Value": ["Spyro Gyra"]} in item["ItemSpecifics"][
        "NameValueList"
    ]


def test_build_item_leaves_out_best_offer_and_pictures_when_absent(app):
    draft = _draft(best_offer=False)
    item = build_item(draft, SETTINGS)

    assert "BestOfferDetails" not in item
    assert "PictureDetails" not in item


def test_check_ready_needs_a_picture_and_business_policies(app, monkeypatch):
    draft = _draft()
    assert check_ready(draft) == ["Add at least one picture before publishing."]

    monkeypatch.setitem(fbc.app.config["EBAY_SETTINGS"], "LISTING", {})
    draft.set_picture_list(["https://i.ebayimg.com/a.jpg"])
    problems = check_ready(draft)
    assert len(problems) == 3
    assert all("PROFILE_ID" in p for p in problems)


def test_summarize_response_splits_errors_warnings_and_fees():
    resp = {
        "Errors": [
            {"SeverityCode": "Error", "LongMessage": "Bad specific."},
            {"SeverityCode": "Warning", "ShortMessage": "Heads up."},
        ],
        "Fees": {
            "Fee": [
                {"Name": "InsertionFee", "Fee": {"value": "0.35"}},
                {"Name": "BoldFee", "Fee": {"value": "0.0"}},
            ]
        },
    }
    assert summarize_response(resp) == {
        "errors": ["Bad specific."],
        "warnings": ["Heads up."],
        "fees": [{"name": "InsertionFee", "amount": 0.35}],
    }


def test_summarize_response_handles_a_single_error_dict():
    resp = {"Errors": {"SeverityCode": "Error", "ShortMessage": "Nope."}}
    assert summarize_response(resp)["errors"] == ["Nope."]


def test_publish_refuses_without_pictures_and_never_calls_ebay(app, monkeypatch):
    calls = []
    monkeypatch.setattr(listing, "_call", lambda *a: calls.append(a))
    draft = _draft()

    summary = publish_draft(draft)

    assert calls == []
    assert summary["errors"] == ["Add at least one picture before publishing."]
    assert draft.status == "draft"


def test_publish_records_the_listing_and_links_the_unit(app, monkeypatch):
    sent = {}

    def fake_call(verb, data):
        sent["verb"] = verb
        sent["data"] = data
        resp = {"ItemID": "397798127145", "StartTime": "2026-10-29T16:00:00.000Z"}
        return resp, summarize_response(resp)

    monkeypatch.setattr(listing, "_call", fake_call)
    draft = _draft(pictures=["https://i.ebayimg.com/a.jpg"])

    summary = publish_draft(draft)

    assert summary["errors"] == []
    assert sent["verb"] == "AddFixedPriceItem"
    assert sent["data"]["Item"]["Title"] == draft.title
    assert draft.status == "published"
    assert draft.ebay_listing_id == "397798127145"
    assert draft.unit.ebay_listing_id == "397798127145"
    ebay_listing = db.session.query(EbayListing).get("397798127145")
    assert ebay_listing.url == "https://www.ebay.com/itm/397798127145"
    assert ebay_listing.price == 5.0
    assert ebay_listing.start_time == datetime.datetime(2026, 10, 29, 16, 0)


def test_publish_failure_leaves_the_draft_unpublished(app, monkeypatch):
    monkeypatch.setattr(
        listing, "_call", lambda verb, data: (None, {"errors": ["No."], "warnings": [], "fees": []})
    )
    draft = _draft(pictures=["https://i.ebayimg.com/a.jpg"])

    summary = publish_draft(draft)

    assert summary["errors"] == ["No."]
    assert draft.status == "draft"
    assert draft.unit.ebay_listing_id is None


def test_ensure_release_details_skips_releases_that_have_labels(app, monkeypatch):
    import discogs_client

    monkeypatch.setattr(discogs_client, "Client", lambda *a, **k: pytest.fail())
    listing.ensure_release_details(_carnaval().discogs_release)


def test_synced_listing_with_fbc_sku_is_linked_to_its_unit(app):
    unit = _carnaval()
    add_ebay_listing(
        {
            "ItemID": "999",
            "Title": "Carnaval",
            "SKU": "fbc-" + str(unit.id),
            "BuyItNowPrice": {"value": "5.00"},
            "ListingDetails": {
                "ViewItemURL": "https://www.ebay.com/itm/999",
                "StartTime": "2024-01-15T10:00:00.000Z",
            },
        }
    )
    assert db.session.query(Unit).get(unit.id).ebay_listing_id == "999"


def test_synced_listing_with_other_sku_is_not_linked(app):
    unit = _carnaval()
    add_ebay_listing(
        {
            "ItemID": "999",
            "Title": "Carnaval",
            "SKU": str(unit.id),
            "BuyItNowPrice": {"value": "5.00"},
            "ListingDetails": {
                "ViewItemURL": "https://www.ebay.com/itm/999",
                "StartTime": "2024-01-15T10:00:00.000Z",
            },
        }
    )
    assert db.session.query(Unit).get(unit.id).ebay_listing_id is None


def test_unit_link_column_offers_list_on_ebay_until_listed(app):
    unit = _carnaval()
    assert "List&nbsp;on&nbsp;eBay" in str(unit.link_column())

    unit.sold = True
    assert "List&nbsp;on&nbsp;eBay" not in str(unit.link_column())


def test_draft_picture_list_round_trips(app):
    draft = EbayDraft(title="t", unit_id=1)
    assert draft.picture_list() == []
    draft.set_picture_list(["a", "b"])
    assert draft.picture_list() == ["a", "b"]
    assert draft.picture_count() == 2
