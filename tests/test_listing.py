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
        pressing="US Original",
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
        "Edition": ["First Edition", "First Pressing"],
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
    assert len(problems) == 1
    assert "TEMPLATE_ITEM_ID" in problems[0]


def test_check_ready_accepts_a_template_listing_instead_of_policies(app, monkeypatch):
    monkeypatch.setitem(
        fbc.app.config["EBAY_SETTINGS"], "LISTING", {"TEMPLATE_ITEM_ID": "397798127145"}
    )
    draft = _draft(pictures=["https://i.ebayimg.com/a.jpg"])
    assert check_ready(draft) == []


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


# GetItem's Item for a listing like https://www.ebay.com/itm/397798127145,
# as ebaysdk's response.dict() presents it (attributes as "_name", element
# text as "value").
FLAT_ITEM = {
    "ItemID": "397798127145",
    "Location": "Georgetown, Texas",
    "PostalCode": "78626",
    "Country": "US",
    "DispatchTimeMax": "2",
    "ShippingDetails": {
        "ShippingType": "Flat",
        "ShippingServiceOptions": {
            "ShippingService": "USPSMedia",
            "ShippingServiceCost": {"_currencyID": "USD", "value": "4.39"},
            "ShippingServicePriority": "1",
            "ExpeditedService": "false",
            "ShippingTimeMin": "2",
            "ShippingTimeMax": "8",
        },
        "SalesTax": {"SalesTaxPercent": "0.0"},
        "ShippingDiscountProfileID": "0",
        "ThirdPartyCheckout": "false",
    },
    "ReturnPolicy": {
        "ReturnsAcceptedOption": "ReturnsNotAccepted",
        "ReturnsAccepted": "No returns accepted",
    },
    "ShippingPackageDetails": {
        "WeightMajor": {"_unit": "lbs", "_measurementSystem": "English", "value": "1"},
    },
}


def test_to_request_turns_ebaysdk_attributes_back_into_request_form():
    assert listing.to_request({"_currencyID": "USD", "value": "4.39"}) == {
        "#text": "4.39",
        "@attrs": {"currencyID": "USD"},
    }
    assert listing.to_request({"A": [{"value": "1"}, "2"], "_skip": "x"}) == {
        "A": [{"#text": "1"}, "2"]
    }


def test_template_from_a_flat_rate_listing_keeps_only_input_fields():
    assert listing.template_from_item(FLAT_ITEM) == {
        "ShippingDetails": {
            "ShippingType": "Flat",
            "ShippingServiceOptions": [
                {
                    "ShippingService": "USPSMedia",
                    "ShippingServicePriority": "1",
                    "ShippingServiceCost": {
                        "#text": "4.39",
                        "@attrs": {"currencyID": "USD"},
                    },
                }
            ],
        },
        "ReturnPolicy": {"ReturnsAcceptedOption": "ReturnsNotAccepted"},
        "DispatchTimeMax": "2",
        "PostalCode": "78626",
        "Location": "Georgetown, Texas",
    }


def test_template_from_a_calculated_listing_sends_the_package_not_the_cost():
    item = {
        "ShippingDetails": {
            "ShippingType": "Calculated",
            "ShippingServiceOptions": [
                {
                    "ShippingService": "USPSMedia",
                    "ShippingServiceCost": {"_currencyID": "USD", "value": "4.39"},
                    "ShippingServicePriority": "1",
                }
            ],
        },
        "ShippingPackageDetails": {
            "WeightMajor": {"_unit": "lbs", "value": "1"},
            "WeightMinor": {"_unit": "oz", "value": "4"},
            "ShippingPackage": "PackageThickEnvelope",
            "ShippingIrregular": "false",
        },
    }
    fields = listing.template_from_item(item)

    assert fields["ShippingDetails"]["ShippingServiceOptions"] == [
        {"ShippingService": "USPSMedia", "ShippingServicePriority": "1"}
    ]
    assert fields["ShippingPackageDetails"]["WeightMinor"] == {
        "#text": "4",
        "@attrs": {"unit": "oz"},
    }
    assert ("Shipping", "USPSMedia, calculated by eBay") in listing.describe_template(
        fields
    )
    assert ("Package weight", "1 lbs 4 oz") in listing.describe_template(fields)


def test_describe_template_for_the_example_listing():
    assert listing.describe_template(listing.template_from_item(FLAT_ITEM)) == [
        ("Shipping", "USPSMedia, $4.39"),
        ("Shipping type", "Flat"),
        ("Returns", "ReturnsNotAccepted"),
        ("Handling time", "2 business days"),
        ("Ships from", "Georgetown, Texas, 78626"),
    ]


def test_build_item_copies_the_template_when_there_are_no_policies(app):
    draft = _draft()
    template = listing.template_from_item(FLAT_ITEM)
    item = build_item(draft, {"POSTAL_CODE": "78628"}, template)

    assert "SellerProfiles" not in item
    assert item["ShippingDetails"] == template["ShippingDetails"]
    assert item["ReturnPolicy"] == {"ReturnsAcceptedOption": "ReturnsNotAccepted"}
    assert item["DispatchTimeMax"] == "2"
    # config.py wins over the template for the location.
    assert item["PostalCode"] == "78628"


def test_build_item_prefers_policies_over_the_template(app):
    draft = _draft()
    item = build_item(draft, SETTINGS, listing.template_from_item(FLAT_ITEM))

    assert "SellerProfiles" in item
    assert "ShippingDetails" not in item


def test_publish_copies_shipping_from_the_template_listing(app, monkeypatch):
    monkeypatch.setitem(
        fbc.app.config["EBAY_SETTINGS"], "LISTING", {"TEMPLATE_ITEM_ID": "397798127145"}
    )
    calls = []

    def fake_call(verb, data):
        calls.append((verb, data))
        if verb == "GetItem":
            resp = {"Item": FLAT_ITEM}
        else:
            resp = {"ItemID": "555", "StartTime": "2026-10-29T16:00:00.000Z"}
        return resp, summarize_response(resp)

    monkeypatch.setattr(listing, "_call", fake_call)
    draft = _draft(pictures=["https://i.ebayimg.com/a.jpg"])

    summary = publish_draft(draft)

    assert summary["errors"] == []
    assert [verb for verb, _ in calls] == ["GetItem", "AddFixedPriceItem"]
    assert calls[0][1] == {"ItemID": "397798127145"}
    sent = calls[1][1]["Item"]
    assert sent["ShippingDetails"]["ShippingServiceOptions"][0]["ShippingService"] == "USPSMedia"
    assert draft.status == "published"


def test_publish_stops_if_the_template_listing_cant_be_read(app, monkeypatch):
    monkeypatch.setitem(
        fbc.app.config["EBAY_SETTINGS"], "LISTING", {"TEMPLATE_ITEM_ID": "1"}
    )
    verbs = []

    def fake_call(verb, data):
        verbs.append(verb)
        return None, {"errors": ["Item not found."], "warnings": [], "fees": []}

    monkeypatch.setattr(listing, "_call", fake_call)
    draft = _draft(pictures=["https://i.ebayimg.com/a.jpg"])

    summary = publish_draft(draft)

    assert verbs == ["GetItem"]
    assert summary["errors"] == ["Couldn't read template listing 1.", "Item not found."]
    assert draft.status == "draft"


def test_listing_details_reads_seller_profiles_off_get_item(monkeypatch):
    resp = {
        "Item": {
            "SellerProfiles": {
                "SellerShippingProfile": {
                    "ShippingProfileID": "111",
                    "ShippingProfileName": "Media Mail",
                },
                "SellerReturnProfile": {
                    "ReturnProfileID": "222",
                    "ReturnProfileName": "No returns",
                },
                "SellerPaymentProfile": {
                    "PaymentProfileID": "333",
                    "PaymentProfileName": "eBay Payments",
                },
            }
        }
    }
    monkeypatch.setattr(listing, "_call", lambda verb, data: (resp, summarize_response(resp)))

    profiles, _, summary = listing.listing_details("397798127145")

    assert profiles == {
        "shipping": ("111", "Media Mail"),
        "return": ("222", "No returns"),
        "payment": ("333", "eBay Payments"),
    }
    assert summary["warnings"] == []


def test_listing_details_without_policies_returns_what_would_be_copied(monkeypatch):
    resp = {"Item": FLAT_ITEM}
    monkeypatch.setattr(listing, "_call", lambda verb, data: (resp, summarize_response(resp)))

    profiles, fields, summary = listing.listing_details("397798127145")

    assert profiles == {"shipping": None, "return": None, "payment": None}
    assert fields == listing.template_from_item(FLAT_ITEM)
    assert summary["warnings"] == []


def test_seller_profiles_reports_what_ebay_returned(monkeypatch):
    resp = {
        "Ack": "Success",
        "SellerProfilePreferences": {
            "SellerProfileOptedIn": "true",
            "SupportedSellerProfiles": {
                "SupportedSellerProfile": {
                    "ProfileID": "111",
                    "ProfileName": "Media Mail",
                    "ProfileType": "SHIPPING",
                }
            },
        },
    }
    monkeypatch.setattr(listing, "_call", lambda verb, data: (resp, summarize_response(resp)))

    profiles, _, details = listing.seller_profiles()

    assert profiles["shipping"] == [("111", "Media Mail")]
    assert details == {
        "ack": "Success",
        "returned_preferences": True,
        "opted_in": "true",
        "profile_count": 1,
    }


def test_trading_asks_for_a_current_api_version(app):
    assert listing.trading().config.get("compatibility") == listing.TRADING_VERSION


def test_escape_request_escapes_text_and_attributes_but_not_tags():
    assert listing.escape_request(
        {
            "Value": ["Stage & Screen", "<b>"],
            "Cost": {"#text": "1 & 2", "@attrs": {"note": 'say "hi"'}},
            "Quantity": 1,
        }
    ) == {
        "Value": ["Stage &amp; Screen", "&lt;b&gt;"],
        "Cost": {"#text": "1 &amp; 2", "@attrs": {"note": "say &quot;hi&quot;"}},
        "Quantity": 1,
    }


def test_listing_request_is_well_formed_xml_with_special_characters(app, monkeypatch):
    """
    Build the real request ebaysdk would send for a draft full of
    characters XML reserves, and check eBay could parse it.
    """
    import xml.etree.ElementTree as ET

    from ebaysdk.connection import BaseConnection

    sent = {}

    class Sent(Exception):
        pass

    def capture(self):
        sent["body"] = self.request.body
        raise Sent()

    monkeypatch.setattr(BaseConnection, "execute_request", capture)
    draft = _draft(
        pictures=["https://i.ebayimg.com/a.jpg?set=1&size=2"],
        title='Simon & Garfunkel - "Bookends" <1968>',
        description="<p>Tiny scuff & a seam split.</p>",
    )
    draft.item_specifics = "Genre: Stage & Screen | Rock\nArtist: Simon & Garfunkel"
    template = listing.template_from_item(
        {
            "ShippingDetails": {
                "ShippingType": "Flat",
                "ShippingServiceOptions": {
                    "ShippingService": "USPSMedia",
                    "ShippingServiceCost": {"_currencyID": "USD", "value": "4.39"},
                },
            }
        }
    )
    item = build_item(draft, {}, template)

    # _call reports the interrupted request as a failure; only what it
    # would have sent matters here.
    listing._call("VerifyAddFixedPriceItem", {"Item": item})

    ns = {"e": "urn:ebay:apis:eBLBaseComponents"}
    root = ET.fromstring(sent["body"])
    sent_item = root.find("e:Item", ns)
    assert sent_item.find("e:Title", ns).text == 'Simon & Garfunkel - "Bookends" <1968>'
    assert sent_item.find("e:Description", ns).text == "<p>Tiny scuff & a seam split.</p>"
    assert sent_item.find("e:PictureDetails/e:PictureURL", ns).text == (
        "https://i.ebayimg.com/a.jpg?set=1&size=2"
    )
    values = [
        v.text for v in sent_item.findall("e:ItemSpecifics/e:NameValueList/e:Value", ns)
    ]
    assert "Stage & Screen" in values
    assert "Simon & Garfunkel" in values
    cost = sent_item.find(
        "e:ShippingDetails/e:ShippingServiceOptions/e:ShippingServiceCost", ns
    )
    assert (cost.text, cost.get("currencyID")) == ("4.39", "USD")


class FakeHTTPResponse:
    def __init__(self, status_code, body=None, headers=None, text=None, reason=""):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.reason = reason
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body


@pytest.fixture
def oauth(monkeypatch):
    """A configured refresh token and an empty access token cache."""
    monkeypatch.setitem(fbc.app.config["EBAY_SETTINGS"], "OAUTH_REFRESH_TOKEN", "refresh")
    monkeypatch.setattr(listing, "_oauth", {"token": None, "expires": None})


def test_oauth_token_is_minted_from_the_refresh_token_and_reused(app, oauth, monkeypatch):
    import requests

    posts = []

    def fake_post(url, **kwargs):
        posts.append((url, kwargs))
        return FakeHTTPResponse(200, {"access_token": "access", "expires_in": 7200})

    monkeypatch.setattr(requests, "post", fake_post)

    assert listing.oauth_token() == "access"
    assert listing.oauth_token() == "access"
    assert len(posts) == 1
    url, kwargs = posts[0]
    assert url == listing.OAUTH_TOKEN_URL
    assert kwargs["auth"] == ("test-app-id", "test-cert-id")
    assert kwargs["data"]["grant_type"] == "refresh_token"
    assert kwargs["data"]["refresh_token"] == "refresh"
    assert kwargs["data"]["scope"] == listing.MEDIA_SCOPE


def test_oauth_token_explains_a_refused_refresh_token(app, oauth, monkeypatch):
    import requests

    monkeypatch.setattr(
        requests,
        "post",
        lambda url, **kw: FakeHTTPResponse(
            400, {"error": "invalid_grant", "error_description": "the token is expired"}
        ),
    )
    with pytest.raises(listing.OAuthError, match="the token is expired"):
        listing.oauth_token()


def test_upload_without_a_refresh_token_says_what_to_configure(app, monkeypatch):
    monkeypatch.setitem(fbc.app.config["EBAY_SETTINGS"], "OAUTH_REFRESH_TOKEN", "")
    monkeypatch.setattr(listing, "_oauth", {"token": None, "expires": None})

    url, summary = listing.upload_picture(None, "front.jpg", "image/jpeg")

    assert url is None
    assert "Connect eBay" in summary["errors"][0]


def test_upload_picture_posts_to_the_media_api(app, oauth, monkeypatch):
    import io

    import requests

    monkeypatch.setattr(listing, "oauth_token", lambda: "access")
    sent = {}

    def fake_post(url, **kwargs):
        sent["url"] = url
        sent.update(kwargs)
        return FakeHTTPResponse(
            201, {"imageUrl": "https://i.ebayimg.com/images/g/abc/s-l1600.jpg"}
        )

    monkeypatch.setattr(requests, "post", fake_post)
    fileobj = io.BytesIO(b"jpeg")

    url, summary = listing.upload_picture(fileobj, "front.jpg", "image/jpeg")

    assert url == "https://i.ebayimg.com/images/g/abc/s-l1600.jpg"
    assert summary["errors"] == []
    assert sent["url"] == listing.MEDIA_URL
    assert sent["headers"] == {"Authorization": "Bearer access"}
    assert sent["files"] == {"image": ("front.jpg", fileobj, "image/jpeg")}


def test_upload_picture_falls_back_to_the_location_header(app, oauth, monkeypatch):
    import requests

    monkeypatch.setattr(listing, "oauth_token", lambda: "access")
    location = "https://apim.ebay.com/commerce/media/v1_beta/image/abc"
    monkeypatch.setattr(
        requests,
        "post",
        lambda url, **kw: FakeHTTPResponse(201, None, {"Location": location}, text=""),
    )
    got = []

    def fake_get(url, **kwargs):
        got.append(url)
        return FakeHTTPResponse(200, {"imageUrl": "https://i.ebayimg.com/x.jpg"})

    monkeypatch.setattr(requests, "get", fake_get)

    url, _ = listing.upload_picture(None, "front.jpg")

    assert got == [location]
    assert url == "https://i.ebayimg.com/x.jpg"


def test_upload_picture_reports_ebay_errors(app, oauth, monkeypatch):
    import requests

    listing._oauth.update(token="stale", expires=datetime.datetime.max)
    monkeypatch.setattr(
        requests,
        "post",
        lambda url, **kw: FakeHTTPResponse(
            401, {"errors": [{"errorId": 1001, "message": "Invalid access token"}]}
        ),
    )

    url, summary = listing.upload_picture(None, "front.jpg")

    assert url is None
    assert summary["errors"] == ["Uploading front.jpg: Invalid access token"]
    # A rejected token isn't reused for the next upload.
    assert listing._oauth["token"] is None


def test_call_reports_a_reply_ebaysdk_cant_read(app, monkeypatch):
    """eBay answering with something other than Trading API XML."""

    class Api:
        response = FakeHTTPResponse(
            404, None, text="<html>Not Found</html>", reason="Not Found"
        )

        def execute(self, verb, data):
            raise AttributeError("'ResponseDataObject' object has no attribute 'Ack'")

    monkeypatch.setattr(listing, "trading", lambda: Api())

    resp, summary = listing._call("GetItem", {"ItemID": "1"})

    assert resp is None
    assert summary["errors"] == [
        "GetItem failed: HTTP 404 Not Found: <html>Not Found</html> (AttributeError)"
    ]


def test_consent_url_asks_for_the_media_scope_through_the_runame(app, monkeypatch):
    from urllib.parse import parse_qs, urlparse

    monkeypatch.setitem(fbc.app.config["EBAY_SETTINGS"], "RUNAME", "Chris-fbc-PRD-runame")
    url = listing.consent_url("xyz")

    assert url.startswith("https://auth.ebay.com/oauth2/authorize?")
    assert parse_qs(urlparse(url).query) == {
        "client_id": ["test-app-id"],
        "redirect_uri": ["Chris-fbc-PRD-runame"],
        "response_type": ["code"],
        "scope": [listing.MEDIA_SCOPE],
        "state": ["xyz"],
    }


def test_consent_url_needs_a_runame(app, monkeypatch):
    monkeypatch.setitem(fbc.app.config["EBAY_SETTINGS"], "RUNAME", "")
    with pytest.raises(listing.OAuthError, match="RUNAME"):
        listing.consent_url("xyz")


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "https://signin.ebay.com/ws/eBayISAPI.dll?ThirdPartyAuthSucessFailure"
            "&isAuthSuccessful=true&state=xyz&code=v%5E1.1%23i%5E1&expires_in=299",
            ("v^1.1#i^1", "xyz"),
        ),
        ("state=xyz&code=v%5E1.1%23i%5E1&expires_in=299", ("v^1.1#i^1", "xyz")),
        ("  v%5E1.1%23i%5E1  ", ("v^1.1#i^1", None)),
        ("", (None, None)),
    ],
)
def test_code_from_finds_the_code_in_what_ebay_returned(text, expected):
    assert listing.code_from(text) == expected


def test_exchange_code_saves_the_refresh_token(app, oauth, monkeypatch):
    import requests

    from fbc.models.ebay import EbayOAuthToken

    monkeypatch.setitem(fbc.app.config["EBAY_SETTINGS"], "RUNAME", "the-runame")
    sent = {}

    def fake_post(url, **kwargs):
        sent.update(kwargs["data"])
        return FakeHTTPResponse(
            200,
            {
                "access_token": "access",
                "expires_in": 7200,
                "refresh_token": "refresh-from-ebay",
                "refresh_token_expires_in": 47304000,
            },
        )

    monkeypatch.setattr(requests, "post", fake_post)

    saved = listing.exchange_code("v^1.1#i^1")

    assert sent == {
        "grant_type": "authorization_code",
        "code": "v^1.1#i^1",
        "redirect_uri": "the-runame",
    }
    assert db.session.query(EbayOAuthToken).one().refresh_token == "refresh-from-ebay"
    assert (saved.expires - saved.created).days == 547
    # The access token that came with it is used straight away.
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("asked again"))
    assert listing.oauth_token() == "access"


def test_exchange_code_reports_a_rejected_code(app, oauth, monkeypatch):
    import requests

    monkeypatch.setattr(
        requests,
        "post",
        lambda url, **kw: FakeHTTPResponse(
            400, {"error": "invalid_grant", "error_description": "code has expired"}
        ),
    )
    with pytest.raises(listing.OAuthError, match="code has expired"):
        listing.exchange_code("old")


def test_oauth_token_prefers_the_saved_refresh_token(app, oauth, monkeypatch):
    import requests

    from fbc.models.ebay import EbayOAuthToken

    db.session.add(EbayOAuthToken(refresh_token="older"))
    db.session.add(EbayOAuthToken(refresh_token="newest"))
    db.session.commit()
    used = []

    def fake_post(url, **kwargs):
        used.append(kwargs["data"]["refresh_token"])
        return FakeHTTPResponse(200, {"access_token": "access", "expires_in": 7200})

    monkeypatch.setattr(requests, "post", fake_post)

    listing.oauth_token()
    assert used == ["newest"]


def test_new_draft_starts_with_the_weight_for_its_record_size(app, monkeypatch):
    unit = _carnaval()
    assert new_draft(unit).weight_oz == 16

    monkeypatch.setitem(
        fbc.app.config["EBAY_SETTINGS"]["LISTING"], "PACKAGE", {"WEIGHT_OZ": {'12"': 22}}
    )
    assert new_draft(unit).weight_oz == 22


def test_new_draft_weight_for_a_seven_inch(app):
    unit = _carnaval(unit_type='7" Single')
    unit.discogs_release.formats = json.dumps([{"name": "Vinyl", "descriptions": ['7"']}])
    assert new_draft(unit).weight_oz == 8


def test_build_item_sends_the_weight_as_pounds_and_ounces(app):
    draft = _draft(weight_oz=20)
    item = build_item(draft, SETTINGS)

    assert item["ShippingPackageDetails"] == {
        "MeasurementUnit": "English",
        "WeightMajor": {"#text": "1", "@attrs": {"unit": "lbs"}},
        "WeightMinor": {"#text": "4", "@attrs": {"unit": "oz"}},
    }


def test_build_item_adds_package_size_from_config(app):
    draft = _draft(weight_oz=8)
    settings = dict(
        SETTINGS,
        PACKAGE={"LENGTH": 13, "WIDTH": 13, "DEPTH": 1, "TYPE": "PackageThickEnvelope"},
    )
    package = build_item(draft, settings)["ShippingPackageDetails"]

    assert package["WeightMajor"]["#text"] == "0"
    assert package["WeightMinor"]["#text"] == "8"
    assert package["PackageLength"] == {"#text": "13", "@attrs": {"unit": "in"}}
    assert package["PackageDepth"] == {"#text": "1", "@attrs": {"unit": "in"}}
    assert package["ShippingPackage"] == "PackageThickEnvelope"


def test_draft_from_before_weights_gets_the_default_weight(app):
    draft = _draft(weight_oz=None)
    package = build_item(draft, SETTINGS)["ShippingPackageDetails"]

    assert (package["WeightMajor"]["#text"], package["WeightMinor"]["#text"]) == ("1", "0")


def test_draft_weight_wins_over_the_template_package(app):
    item = build_item(
        _draft(weight_oz=24),
        {"TEMPLATE_ITEM_ID": "1"},
        {"ShippingPackageDetails": {"WeightMajor": {"#text": "9"}}},
    )
    assert item["ShippingPackageDetails"]["WeightMajor"]["#text"] == "1"
    assert item["ShippingPackageDetails"]["WeightMinor"]["#text"] == "8"


@pytest.mark.parametrize(
    "formats,weight",
    [
        # 12" LP, EP, Single and Maxi-Single: one disc.
        ([{"name": "Vinyl", "qty": "1", "descriptions": ["LP", "Album"]}], 16),
        ([{"name": "Vinyl", "qty": "1", "descriptions": ['12"', "EP"]}], 16),
        ([{"name": "Vinyl", "qty": "1", "descriptions": ['12"', "Single"]}], 16),
        ([{"name": "Vinyl", "qty": "1", "descriptions": ['12"', "Maxi-Single"]}], 16),
        # Double and triple LPs.
        ([{"name": "Vinyl", "qty": "2", "descriptions": ["LP", "Album"]}], 32),
        ([{"name": "Vinyl", "qty": "3", "descriptions": ["LP", "Album"]}], 48),
        # Discs listed as separate formats add up; a bonus CD doesn't count.
        (
            [
                {"name": "Vinyl", "qty": "1", "descriptions": ["LP"]},
                {"name": "Vinyl", "qty": "1", "descriptions": ["LP"]},
                {"name": "CD", "qty": "1"},
            ],
            32,
        ),
    ],
)
def test_default_weight_is_16_oz_per_12_inch_disc(app, formats, weight):
    unit = _carnaval()
    unit.discogs_release.formats = json.dumps(formats)
    assert new_draft(unit).weight_oz == weight


@pytest.mark.parametrize(
    "unit_type,discs",
    [
        ('12" LP', 1),
        ('12" Double LP', 2),
        ('12" Triple LP', 3),
        ('12" 2xLP', 2),
        ('12" 2 x LP', 2),
        ('7" Single', 1),
    ],
)
def test_disc_count_falls_back_to_the_unit_type(unit_type, discs):
    assert listing.disc_count(Unit(name="u", unit_type=unit_type)) == discs


def test_draft_from_before_weights_counts_its_discs(app):
    draft = _draft(weight_oz=None)
    draft.unit.discogs_release.formats = json.dumps(
        [{"name": "Vinyl", "qty": "2", "descriptions": ["LP"]}]
    )
    package = build_item(draft, SETTINGS)["ShippingPackageDetails"]

    assert (package["WeightMajor"]["#text"], package["WeightMinor"]["#text"]) == ("2", "0")


def test_era_of_a_reissue_comes_from_its_master_release(app):
    unit = _carnaval()
    unit.discogs_release.year = 2016
    unit.discogs_release.master_year = 1980
    specifics = build_item_specifics(unit)

    assert specifics["Release Year"] == ["2016"]
    assert specifics["Era"] == ["1980s"]
    assert specifics["Style"][0] == "1980s"


@pytest.mark.parametrize("master_year", [None, 0, -1])
def test_era_falls_back_to_the_release_year(app, master_year):
    unit = _carnaval()
    unit.discogs_release.year = 1992
    unit.discogs_release.master_year = master_year

    assert build_item_specifics(unit)["Era"] == ["1990s"]


def test_no_era_when_discogs_has_no_year(app):
    unit = _carnaval()
    unit.discogs_release.year = 0
    unit.discogs_release.master_year = 0
    specifics = build_item_specifics(unit)

    assert "Era" not in specifics
    assert "Release Year" not in specifics
    assert specifics["Style"] == ["Fusion", "Jazz Funk"]


@pytest.mark.parametrize(
    "pressing,editions,country",
    [
        ("US Original", ["First Edition", "First Pressing"], "United States"),
        ("UK Reissue", ["Reissue"], "United Kingdom"),
        ("USA original pressing", ["First Edition", "First Pressing"], "United States"),
        ("JP Reissue w/ OBI", ["Reissue"], "Japan"),
        ("Japanese Original", ["First Edition", "First Pressing"], "Japan"),
        ("Germany Reissue", ["Reissue"], "Germany"),
        ("West Germany Original", ["First Edition", "First Pressing"], "West Germany"),
        ("Canada", [], "Canada"),
        ("Original", ["First Edition", "First Pressing"], None),
        ("Reissue, 180g", ["Reissue"], None),
        ("Original Reissue", ["First Edition", "First Pressing", "Reissue"], None),
        ("Club Edition", [], None),
        ("LTD numbered", [], None),
        ("us original", ["First Edition", "First Pressing"], None),
        ("", [], None),
        (None, [], None),
    ],
)
def test_parse_pressing(pressing, editions, country):
    assert listing.parse_pressing(pressing) == (editions, country)


@pytest.mark.parametrize(
    "unit_type,types",
    [
        ('12" LP', ["LP"]),
        ('12" Double LP', ["LP"]),
        ('12" 2xLP', ["LP"]),
        ('12" EP', ["EP"]),
        ('7" Single', ["Single"]),
        ('12" Maxi-Single', ["Maxi-Single"]),
        ('12" lp', ["LP"]),
        ('10"', []),
        (None, []),
    ],
)
def test_parse_type(unit_type, types):
    assert listing.parse_type(unit_type) == types


def test_pressing_country_wins_over_discogs(app):
    unit = _carnaval(pressing="Canada Reissue")
    specifics = build_item_specifics(unit)

    assert specifics["Country of Origin"] == ["Canada"]
    assert specifics["Edition"] == ["Reissue"]


def test_discogs_country_when_pressing_names_none(app):
    unit = _carnaval(pressing="Reissue")
    unit.discogs_release.country = "UK"

    assert build_item_specifics(unit)["Country of Origin"] == ["United Kingdom"]


def test_no_edition_when_pressing_says_neither(app):
    assert "Edition" not in build_item_specifics(_carnaval(pressing="Club Edition"))


def test_type_comes_from_unit_type_before_discogs(app):
    # Discogs calls it an LP; the Unit says it's a Maxi-Single.
    unit = _carnaval(unit_type='12" Maxi-Single')

    assert build_item_specifics(unit)["Type"] == ["Maxi-Single"]


def test_type_falls_back_to_discogs(app):
    unit = _carnaval(unit_type='12"')

    assert build_item_specifics(unit)["Type"] == ["LP"]


def test_title_uses_the_parsed_edition(app):
    title = build_title(_carnaval(pressing="UK Reissue", unit_type='12" Double LP'))

    assert title.startswith("Spyro Gyra - Carnaval 1980 Reissue Vinyl LP ")
    assert "UK" not in title
