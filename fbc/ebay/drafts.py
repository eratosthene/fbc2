"""Building an eBay Draft's Item (the container the Trading API takes), from a
Unit, the listing settings and an optional template item."""

import json
import logging


from fbc.ebay import api, describe, skus, vocab

logger = logging.getLogger()


POLICY_KEYS = ("SHIPPING_PROFILE_ID", "RETURN_PROFILE_ID", "PAYMENT_PROFILE_ID")


def uses_policies(settings):
    """Whether config.py names business policies for new listings."""
    return all(settings.get(key) for key in POLICY_KEYS)


def build_item(draft, settings, template=None):
    """
    The Item container for AddFixedPriceItem / VerifyAddFixedPriceItem.
    `settings` is EBAY_SETTINGS["LISTING"] from config.py. Shipping, returns
    and handling time come from the business policies named there or, for an
    account without business policies, from `template`: the fields
    template_fields() copied from an existing listing.
    """
    specifics = describe.parse_specifics(draft.item_specifics)
    item = {
        "Title": draft.title,
        "Description": draft.description or "",
        "PrimaryCategory": {
            "CategoryID": settings.get("CATEGORY_ID", vocab.DEFAULT_CATEGORY_ID)
        },
        "StartPrice": f"{draft.price:.2f}" if draft.price is not None else "",
        "ConditionID": vocab.CONDITION_USED,
        "Country": settings.get("COUNTRY", "US"),
        "Currency": settings.get("CURRENCY", "USD"),
        "ListingDuration": "GTC",
        "ListingType": "FixedPriceItem",
        "Quantity": 1,
        "SKU": skus.sku(draft.unit_id),
        "ItemSpecifics": {
            "NameValueList": [
                {"Name": name, "Value": values} for name, values in specifics.items()
            ]
        },
    }
    if uses_policies(settings):
        item["SellerProfiles"] = {
            "SellerShippingProfile": {
                "ShippingProfileID": settings["SHIPPING_PROFILE_ID"]
            },
            "SellerReturnProfile": {"ReturnProfileID": settings["RETURN_PROFILE_ID"]},
            "SellerPaymentProfile": {
                "PaymentProfileID": settings["PAYMENT_PROFILE_ID"]
            },
        }
    if settings.get("POSTAL_CODE"):
        item["PostalCode"] = settings["POSTAL_CODE"]
    if settings.get("LOCATION"):
        item["Location"] = settings["LOCATION"]
    package = build_package(draft, settings)
    if package:
        item["ShippingPackageDetails"] = package
    if template and not uses_policies(settings):
        for key, value in template.items():
            item.setdefault(key, value)
    if draft.best_offer:
        item["BestOfferDetails"] = {"BestOfferEnabled": "true"}
    pictures = draft.picture_list()
    if pictures:
        item["PictureDetails"] = {"PictureURL": pictures}
    return item


def default_weight(specifics, settings, discs=1):
    """Starting package weight in ounces for `discs` records with `specifics`."""
    weights = (settings.get("PACKAGE") or vocab.DEFAULT_PACKAGE).get("WEIGHT_OZ") or {}
    size = (specifics.get("Record Size") or ['12"'])[0]
    per_disc = weights.get(size, weights.get('12"'))
    if per_disc is None:
        return None
    return per_disc * discs


def build_package(draft, settings):
    """
    Item.ShippingPackageDetails for the draft's weight (whole ounces, sent
    as pounds plus ounces) and any package size set in config.py. A draft
    made before drafts had a weight gets the default for its record size.
    """
    weight = draft.weight_oz
    if weight is None:
        weight = default_weight(
            describe.parse_specifics(draft.item_specifics),
            settings,
            describe.disc_count(draft.unit),
        )
    if not weight:
        return None
    lbs, oz = divmod(int(weight), 16)
    package = {
        "MeasurementUnit": "English",
        "WeightMajor": {"#text": str(lbs), "@attrs": {"unit": "lbs"}},
        "WeightMinor": {"#text": str(oz), "@attrs": {"unit": "oz"}},
    }
    config = settings.get("PACKAGE") or {}
    for key, field in (
        ("LENGTH", "PackageLength"),
        ("WIDTH", "PackageWidth"),
        ("DEPTH", "PackageDepth"),
    ):
        if config.get(key):
            package[field] = {"#text": str(config[key]), "@attrs": {"unit": "in"}}
    if config.get("TYPE"):
        package["ShippingPackage"] = config["TYPE"]
    return package


def to_request(value):
    """
    Turn part of an ebaysdk response dict back into request form: ebaysdk
    reads <Cost currencyID="USD">4.39</Cost> as {"_currencyID": "USD",
    "value": "4.39"}, but sends {"#text": ..., "@attrs": {...}}.
    """
    if isinstance(value, list):
        return [to_request(v) for v in value]
    if not isinstance(value, dict):
        return value
    if "value" in value:
        out = {"#text": value["value"]}
        attrs = {k[1:]: v for k, v in value.items() if k.startswith("_")}
        if attrs:
            out["@attrs"] = attrs
        return out
    return {k: to_request(v) for k, v in value.items() if not k.startswith("_")}


# What gets copied from a template listing. GetItem returns plenty more
# (calculated rates, discount profile IDs, sales tax...), much of it output
# only, so only fields AddFixedPriceItem takes as input are kept.
SHIPPING_OPTION_FIELDS = (
    "ShippingService",
    "ShippingServicePriority",
    "ShippingServiceCost",
    "ShippingServiceAdditionalCost",
    "FreeShipping",
)


SHIPPING_COST_FIELDS = ("ShippingServiceCost", "ShippingServiceAdditionalCost")


# Shipping types where the seller sets the domestic cost; otherwise eBay
# calculates it from the package details.
FLAT_SHIPPING_TYPES = ("Flat", "FlatDomesticCalculatedInternational")


PACKAGE_FIELDS = (
    "MeasurementUnit",
    "PackageDepth",
    "PackageLength",
    "PackageWidth",
    "ShippingIrregular",
    "ShippingPackage",
    "WeightMajor",
    "WeightMinor",
)


RETURN_FIELDS = (
    "ReturnsAcceptedOption",
    "ReturnsWithinOption",
    "RefundOption",
    "ShippingCostPaidByOption",
)


ITEM_FIELDS = ("DispatchTimeMax", "PostalCode", "Location")


def template_from_item(item):
    """
    The domestic shipping, returns, handling time and location of a listing
    (the Item from GetItem), as Item fields for build_item().
    """
    fields = {}
    shipping = item.get("ShippingDetails") or {}
    shipping_type = shipping.get("ShippingType")
    options = []
    for option in api._as_list(shipping.get("ShippingServiceOptions")):
        o = {k: to_request(option[k]) for k in SHIPPING_OPTION_FIELDS if k in option}
        if shipping_type not in FLAT_SHIPPING_TYPES:
            for k in SHIPPING_COST_FIELDS:
                o.pop(k, None)
        options.append(o)
    if shipping_type and options:
        fields["ShippingDetails"] = {
            "ShippingType": shipping_type,
            "ShippingServiceOptions": options,
        }
    package = item.get("ShippingPackageDetails") or {}
    if shipping_type not in FLAT_SHIPPING_TYPES and package:
        fields["ShippingPackageDetails"] = {
            k: to_request(package[k]) for k in PACKAGE_FIELDS if k in package
        }
    returns = item.get("ReturnPolicy") or {}
    returns = {k: returns[k] for k in RETURN_FIELDS if returns.get(k)}
    if returns:
        fields["ReturnPolicy"] = returns
    for k in ITEM_FIELDS:
        if item.get(k):
            fields[k] = item[k]
    return fields


def describe_template(fields):
    """The template fields as (label, text) rows for people to check."""
    rows = []
    shipping = fields.get("ShippingDetails")
    if shipping:
        for o in shipping["ShippingServiceOptions"]:
            if o.get("FreeShipping") == "true":
                cost = "free"
            elif "ShippingServiceCost" in o:
                cost = "$" + o["ShippingServiceCost"]["#text"]
            else:
                cost = "calculated by eBay"
            rows.append(("Shipping", o.get("ShippingService", "?") + ", " + cost))
        rows.append(("Shipping type", shipping["ShippingType"]))
    package = fields.get("ShippingPackageDetails")
    if package:
        weight = [
            package[k]["#text"] + " " + package[k].get("@attrs", {}).get("unit", "")
            for k in ("WeightMajor", "WeightMinor")
            if k in package
        ]
        rows.append(("Package weight", " ".join(w.strip() for w in weight)))
    returns = fields.get("ReturnPolicy")
    if returns:
        text = returns.get("ReturnsAcceptedOption", "")
        if returns.get("ReturnsWithinOption"):
            text += ", " + returns["ReturnsWithinOption"]
        rows.append(("Returns", text))
    if fields.get("DispatchTimeMax"):
        rows.append(("Handling time", fields["DispatchTimeMax"] + " business days"))
    location = ", ".join(fields[k] for k in ("Location", "PostalCode") if fields.get(k))
    if location:
        rows.append(("Ships from", location))
    return rows


def ensure_release_details(release):
    """
    Releases synced before fbc stored labels and country don't have them;
    fetch them from Discogs the first time a listing needs them.
    """
    if not release or release.labels is not None:
        return
    from fbc import db
    from fbc import discogs_client

    try:
        r = discogs_client.release(release.release_id)
        release.labels = json.dumps(
            [
                {"name": label.get("name"), "catno": label.get("catno")}
                for label in r.get("labels") or []
            ]
        )
        release.country = r.get("country")
        db.session.commit()
    except Exception as e:
        logger.warning(
            "Couldn't fetch labels for release "
            + str(release.release_id)
            + ": "
            + str(e)
        )


def check_ready(draft):
    """Problems fbc can spot before asking eBay; publishing needs none."""
    problems = []
    if not draft.picture_list():
        problems.append("Add at least one picture before publishing.")
    if draft.price is None:
        problems.append("Set a price.")
    settings = api.listing_settings()
    if not uses_policies(settings) and not settings.get("TEMPLATE_ITEM_ID"):
        problems.append(
            "Set EBAY_SETTINGS['LISTING']['TEMPLATE_ITEM_ID'] in config.py to a"
            " listing whose shipping and returns new listings should copy."
        )
    return problems


def template_fields(settings):
    """
    (fields, summary): the template listing's shipping and returns for
    build_item(), or ({}, empty summary) when business policies are used.
    """
    summary = {"errors": [], "warnings": [], "fees": []}
    item_id = settings.get("TEMPLATE_ITEM_ID")
    if uses_policies(settings) or not item_id:
        return {}, summary
    resp, summary = api._call("GetItem", {"ItemID": item_id})
    if resp is None:
        summary["errors"].insert(
            0, "Couldn't read template listing " + str(item_id) + "."
        )
        return {}, summary
    return template_from_item(resp.get("Item") or {}), summary


def listing_details(item_id):
    """
    What an existing listing uses for shipping and returns, from GetItem:
    (business policies as {"shipping"/"return"/"payment": (id, name) or
    None}, the fields template_from_item() would copy, summary).
    """
    resp, summary = api._call("GetItem", {"ItemID": item_id})
    profiles = {"shipping": None, "return": None, "payment": None}
    if resp is None:
        return profiles, {}, summary
    item = resp.get("Item") or {}
    sp = item.get("SellerProfiles") or {}
    for kind, container, prefix in (
        ("shipping", "SellerShippingProfile", "ShippingProfile"),
        ("return", "SellerReturnProfile", "ReturnProfile"),
        ("payment", "SellerPaymentProfile", "PaymentProfile"),
    ):
        p = sp.get(container)
        if p and p.get(prefix + "ID"):
            profiles[kind] = (p[prefix + "ID"], p.get(prefix + "Name"))
    return profiles, template_from_item(item), summary


def new_draft(unit):
    """A fresh EbayDraft for `unit`, filled in from the Unit and Discogs."""
    from fbc.models.ebay import EbayDraft

    ensure_release_details(unit.discogs_release)
    settings = api.listing_settings()
    specifics = describe.build_item_specifics(unit, settings.get("DEFAULT_SPECIFICS"))
    return EbayDraft(
        unit=unit,
        title=describe.build_title(unit),
        price=unit.retail_price,
        best_offer=settings.get("BEST_OFFER", True),
        description=describe.build_description(unit),
        item_specifics=describe.format_specifics(specifics),
        weight_oz=default_weight(specifics, settings, describe.disc_count(unit)),
        status="draft",
    )
