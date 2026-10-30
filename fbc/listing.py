"""
Build an eBay listing for a Unit.

Everything here is about turning what fbc already knows about a record (the
Unit plus its Discogs release) into the fields eBay wants: the item specifics
shown on the listing page, an 80-character title, and the Item container the
Trading API's AddFixedPriceItem / VerifyAddFixedPriceItem calls take.

Item specifics are kept as plain text, one per line, in the form

    Name: value | value

so they can be read and edited in an ordinary textarea before publishing.
"""

import datetime
import json
import logging
import re
from xml.sax.saxutils import escape

logger = logging.getLogger()

# Vinyl Records (Music > Vinyl Records) on ebay.com.
DEFAULT_CATEGORY_ID = "176985"
CONDITION_USED = 3000

# Item specifics that fbc can't work out from Discogs and that are the same on
# almost every listing. EBAY_SETTINGS["LISTING"]["DEFAULT_SPECIFICS"] in
# config.py replaces these.
DEFAULT_SPECIFICS = {
    "Format": ["Record"],
    "Material": ["Vinyl"],
    "Case Type": ["Cardboard Sleeve"],
    "Color": ["Black"],
}

# Goldmine grade abbreviations, as Unit.grading stores them, to the wording
# eBay uses for the Record Grading / Sleeve Grading specifics. This is also
# the wording Discogs uses for its Media/Sleeve Condition fields.
GRADES = {
    "M": "Mint (M)",
    "NM": "Near Mint (NM or M-)",
    "M-": "Near Mint (NM or M-)",
    "NM or M-": "Near Mint (NM or M-)",
    "EX": "Excellent (EX)",
    "VG+": "Very Good Plus (VG+)",
    "VG": "Very Good (VG)",
    "G+": "Good Plus (G+)",
    "G": "Good (G)",
    "F": "Fair (F)",
    "P": "Poor (P)",
}

# Discogs genres that eBay splits up.
GENRES = {
    "Funk / Soul": ["Funk", "Soul"],
    "Folk, World, & Country": ["Folk", "World", "Country"],
}

# Country codes (as Discogs and Unit.pressing use them) and adjectives, to
# the names eBay's Country of Origin uses. Anything else passes through.
COUNTRIES = {
    "US": "United States",
    "USA": "United States",
    "American": "United States",
    "UK": "United Kingdom",
    "GB": "United Kingdom",
    "British": "United Kingdom",
    "English": "United Kingdom",
    "CA": "Canada",
    "CAN": "Canada",
    "Canadian": "Canada",
    "JP": "Japan",
    "JPN": "Japan",
    "Japanese": "Japan",
    "DE": "Germany",
    "GER": "Germany",
    "German": "Germany",
    "FR": "France",
    "French": "France",
    "IT": "Italy",
    "Italian": "Italy",
    "NL": "Netherlands",
    "Dutch": "Netherlands",
    "AU": "Australia",
    "AUS": "Australia",
    "Australian": "Australia",
    "NZ": "New Zealand",
    "ES": "Spain",
    "Spanish": "Spain",
    "SE": "Sweden",
    "Swedish": "Sweden",
    "BR": "Brazil",
    "Brazilian": "Brazil",
    "MX": "Mexico",
    "Mexican": "Mexico",
    "EU": "Europe",
    "European": "Europe",
}
# Country names that are more than one word, so "West Germany Original"
# isn't read as a country called "West".
TWO_WORD_COUNTRIES = [
    "United States",
    "United Kingdom",
    "West Germany",
    "East Germany",
    "New Zealand",
    "South Africa",
    "South Korea",
]
# Words a Pressing can start with that aren't countries.
NOT_COUNTRIES = {
    "Original",
    "Reissue",
    "Repress",
    "Remaster",
    "Remastered",
    "First",
    "Second",
    "Early",
    "Late",
    "Later",
    "Promo",
    "Promotional",
    "Limited",
    "Club",
    "Test",
    "White",
    "Mono",
    "Stereo",
    "Unofficial",
    "Bootleg",
    "Deluxe",
    "Import",
}

SIZES = ['12"', '10"', '7"']
TYPES = ["LP", "EP", "Single", "Maxi-Single"]
CHANNELS = ["Stereo", "Mono", "Quadraphonic"]
COLORS = [
    "Red",
    "Blue",
    "Green",
    "Yellow",
    "White",
    "Clear",
    "Orange",
    "Purple",
    "Pink",
    "Gold",
    "Silver",
    "Grey",
    "Brown",
]

TITLE_MAX = 80

# A draft's starting package weight in ounces per disc, by record size,
# packing included: a 12" double LP starts at 32 oz. eBay needs a weight for
# calculated shipping. Replaced by EBAY_SETTINGS["LISTING"]["PACKAGE"] in
# config.py, which can also give LENGTH/WIDTH/DEPTH in inches and a
# ShippingPackage TYPE.
DEFAULT_PACKAGE = {"WEIGHT_OZ": {'12"': 16, '10"': 16, '7"': 8}}


def clean_name(name):
    """Drop the " (2)" Discogs adds to tell same-named artists/labels apart."""
    return re.sub(r"\s*\(\d+\)$", "", name or "").strip()


def split_grading(grading):
    """
    Split Unit.grading ("VG+/VG+", "NM or M-/VG", "VG+") into
    (record, sleeve) abbreviations; either may be None.
    """
    if not grading:
        return None, None
    parts = [p.strip() for p in grading.split("/", 1)]
    record = parts[0] or None
    sleeve = parts[1] if len(parts) > 1 and parts[1] else None
    return record, sleeve


def grade_name(abbrev):
    if not abbrev:
        return None
    return GRADES.get(abbrev, abbrev)


def _format_descriptions(release):
    """Every Discogs format description and free-text note on the release."""
    if not release or not release.formats:
        return [], ""
    try:
        formats = json.loads(release.formats)
    except ValueError:
        return [], ""
    descriptions = []
    text = []
    for f in formats:
        descriptions.extend(f.get("descriptions") or [])
        if f.get("text"):
            text.append(f["text"])
    return descriptions, " ".join(text)


def _labels(release):
    if not release or not release.labels:
        return []
    try:
        return json.loads(release.labels)
    except ValueError:
        return []


def parse_pressing(pressing):
    """
    (Edition values, Country of Origin or None) from Unit.pressing, e.g.
    "US Original" -> (["First Edition", "First Pressing"], "United States").
    Country is the pressing's leading country code (US, UK, JP...) or
    leading capitalized word ("Germany Reissue"), without checking that the
    word really is a country.
    """
    pressing = (pressing or "").strip()
    editions = []
    lowered = pressing.lower()
    if "original" in lowered:
        editions += ["First Edition", "First Pressing"]
    if "reissue" in lowered:
        editions.append("Reissue")
    country = None
    for name in TWO_WORD_COUNTRIES:
        if lowered.startswith(name.lower()):
            country = name
            break
    if country is None:
        m = re.match(r"[A-Za-z]+", pressing)
        word = m.group(0) if m else ""
        if word in COUNTRIES:
            # A code ("US", "JP") or adjective ("Japanese"), as written.
            country = COUNTRIES[word]
        elif (
            len(word) > 2
            and word[0].isupper()
            and not word.isupper()
            and word not in NOT_COUNTRIES
        ):
            # Any other capitalized word is taken to be a country's name.
            country = word
    return editions, country


def parse_type(unit_type):
    """
    The eBay Type values in Unit.unit_type: '12" Double LP' -> ["LP"],
    '12" 2xLP' -> ["LP"], '12" Maxi-Single' -> ["Maxi-Single"].
    """
    by_name = {t.lower(): t for t in TYPES}
    found = []
    for token in re.split(r"[\s/,+&]+", unit_type or ""):
        token = re.sub(r"^\d*\s*[x×]", "", token).strip(" ()").lower()
        if token in by_name:
            found.append(by_name[token])
    return list(dict.fromkeys(found))


def decade(year):
    if not year:
        return None
    return str(year // 10 * 10) + "s"


def build_item_specifics(unit, defaults=None):
    """
    The item specifics for `unit`, as an ordered dict of name -> list of
    values. Anything fbc doesn't know is left out rather than guessed.
    """
    release = unit.discogs_release
    specifics = {}

    def put(name, *values):
        values = [v for v in values if v]
        if values and name not in specifics:
            specifics[name] = list(dict.fromkeys(values))

    if release:
        put("Artist", *[clean_name(a.name) for a in release.artists])
        put("Release Title", release.title)
        labels = _labels(release)
        put("Record Label", *[clean_name(label["name"]) for label in labels[:1]])
        catnos = [
            label["catno"]
            for label in labels[:1]
            if label.get("catno") and label["catno"].lower() != "none"
        ]
        put("Catalog Number", *catnos)
        put("MPN", *catnos)
        if release.year:
            put("Release Year", str(release.year))
        # Era (and the decade in Style) is when the music came out, so a
        # reissue keeps its original's: the master release's year when
        # Discogs has one.
        original_year = release.year
        if release.master_year and release.master_year > 0:
            original_year = release.master_year
        put("Era", decade(original_year))
        genres = []
        for g in release.genres:
            genres.extend(GENRES.get(g.name, [g.name]))
        put("Genre", *genres)
        put("Style", decade(original_year), *[s.name for s in release.styles])
    # Where the copy was pressed: Discogs' country for the release, else the
    # Pressing's leading country.
    editions, pressing_country = parse_pressing(unit.pressing)
    discogs_country = release.country if release else None
    put(
        "Country of Origin",
        COUNTRIES.get(discogs_country, discogs_country) or pressing_country,
    )

    descriptions, text = _format_descriptions(release)
    unit_type = unit.unit_type or ""
    put("Type", *record_types(unit))
    put("Record Size", *[s for s in SIZES if s in descriptions])
    put("Record Size", *[s for s in SIZES if unit_type.startswith(s)])
    for d in descriptions:
        m = re.match(r"(\d+)", d)
        if m and "RPM" in d:
            put("Speed", m.group(1) + " RPM")
    if any(t.endswith("LP") for t in specifics.get("Type", [])):
        put("Speed", "33 RPM")
    put("Number of Audio Channels", *[c for c in CHANNELS if c in descriptions])
    put("Color", *[c for c in COLORS if c.lower() in text.lower()])

    put("Vinyl Matrix Number", unit.matrix)
    put("Edition", *editions)
    record, sleeve = split_grading(unit.grading)
    put("Record Grading", grade_name(record))
    put("Sleeve Grading", grade_name(sleeve))
    put("Inlay Condition", grade_name(sleeve))

    for name, values in (defaults if defaults is not None else DEFAULT_SPECIFICS).items():
        put(name, *values)
    return specifics


def format_specifics(specifics):
    return "\n".join(
        name + ": " + " | ".join(values) for name, values in specifics.items()
    )


def parse_specifics(text):
    """The inverse of format_specifics. Blank and malformed lines are skipped."""
    specifics = {}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        name, values = line.split(":", 1)
        name = name.strip()
        values = [v.strip() for v in values.split("|") if v.strip()]
        if name and values:
            specifics[name] = values
    return specifics


def build_title(unit):
    """
    A title in the house style, e.g.
    "Spyro Gyra - Carnaval 1980 First Edition Vinyl LP Fusion Jazz-Funk VG+"
    for a "US Original" pressing, dropping styles and then the edition until
    it fits in 80 characters.
    """
    release = unit.discogs_release
    if release:
        artist = " / ".join(clean_name(a.name) for a in release.artists)
        head = (artist + " - " if artist else "") + release.title
        year = str(release.year) if release.year else ""
        styles = [s.name.replace(" ", "-") for s in release.styles]
    else:
        head = unit.name
        year = ""
        styles = []
    kind = (record_types(unit) or [""])[0]
    record, _ = split_grading(unit.grading)
    # "First Edition" or "Reissue", as parsed for the Edition specific.
    edition = (parse_pressing(unit.pressing)[0] or [""])[0]

    def assemble(edition, styles):
        parts = [head, year, edition, "Vinyl", kind] + styles + [record or ""]
        return " ".join(p for p in parts if p)

    while styles and len(assemble(edition, styles)) > TITLE_MAX:
        styles = styles[:-1]
    title = assemble(edition, styles)
    if len(title) > TITLE_MAX:
        title = assemble("", [])
    return title[:TITLE_MAX].strip()


def build_description(unit):
    if not unit.description:
        return ""
    return "<p>" + unit.description + "</p>"


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
    specifics = parse_specifics(draft.item_specifics)
    item = {
        "Title": draft.title,
        "Description": draft.description or "",
        "PrimaryCategory": {
            "CategoryID": settings.get("CATEGORY_ID", DEFAULT_CATEGORY_ID)
        },
        "StartPrice": f"{draft.price:.2f}" if draft.price is not None else "",
        "ConditionID": CONDITION_USED,
        "Country": settings.get("COUNTRY", "US"),
        "Currency": settings.get("CURRENCY", "USD"),
        "ListingDuration": "GTC",
        "ListingType": "FixedPriceItem",
        "Quantity": 1,
        "SKU": sku(draft.unit_id),
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


def _discs_in_text(text):
    """A disc count written in free text: "Double", "Triple", "2xLP", "2 x LP"."""
    text = (text or "").lower()
    if "triple" in text:
        return 3
    if "double" in text:
        return 2
    m = re.search(r"\b([2-9])\s*[x×]?\s*lp", text)
    if m:
        return int(m.group(1))
    return None


def disc_count(unit):
    """
    How many records the Unit is: the quantities of its Discogs Vinyl
    formats ("2 x Vinyl, LP"), else a count in its Unit Type ("Double",
    "Triple", "2xLP"), else 1.
    """
    release = unit.discogs_release
    if release and release.formats:
        try:
            formats = json.loads(release.formats)
        except ValueError:
            formats = []
        count = 0
        for f in formats:
            if f.get("name") == "Vinyl":
                try:
                    count += int(f.get("qty") or 1)
                except ValueError:
                    count += 1
        if count:
            return count
    return _discs_in_text(unit.unit_type) or 1


# eBay's Type for an LP that's more than one disc.
MULTI_DISC_TYPES = {2: "Double LP", 3: "Triple LP"}


def record_types(unit):
    """
    The Type values for `unit`: from its Unit Type, else Discogs' format
    descriptions, with an LP of 2 or 3 discs (see disc_count) made a
    "Double LP" or "Triple LP".
    """
    descriptions, _ = _format_descriptions(unit.discogs_release)
    types = parse_type(unit.unit_type) or [t for t in TYPES if t in descriptions]
    multi = MULTI_DISC_TYPES.get(disc_count(unit))
    if multi and types in ([], ["LP"]):
        return [multi]
    return types


def default_weight(specifics, settings, discs=1):
    """Starting package weight in ounces for `discs` records with `specifics`."""
    weights = (settings.get("PACKAGE") or DEFAULT_PACKAGE).get("WEIGHT_OZ") or {}
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
            parse_specifics(draft.item_specifics), settings, disc_count(draft.unit)
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
    for option in _as_list(shipping.get("ShippingServiceOptions")):
        o = {
            k: to_request(option[k]) for k in SHIPPING_OPTION_FIELDS if k in option
        }
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


SKU_PREFIX = "fbc-"


def sku(unit_id):
    """The Custom label (SKU) fbc puts on a listing so it can find the Unit."""
    return SKU_PREFIX + str(unit_id)


def unit_id_from_sku(value):
    if value and value.startswith(SKU_PREFIX) and value[len(SKU_PREFIX) :].isdigit():
        return int(value[len(SKU_PREFIX) :])
    return None


def ensure_release_details(release):
    """
    Releases synced before fbc stored labels and country don't have them;
    fetch them from Discogs the first time a listing needs them.
    """
    if not release or release.labels is not None:
        return
    from fbc import app, db
    import discogs_client

    try:
        d = discogs_client.Client(
            app.config["DISCOGS_SETTINGS"]["USER_AGENT"],
            user_token=app.config["DISCOGS_SETTINGS"]["USER_TOKEN"],
        )
        r = d.release(release.release_id)
        release.labels = json.dumps(
            [{"name": label.name, "catno": label.catno} for label in r.labels]
        )
        release.country = r.country
        db.session.commit()
    except Exception as e:
        logger.warning(
            "Couldn't fetch labels for release " + str(release.release_id) + ": " + str(e)
        )


# ebaysdk asks for Trading API version 837 (2013) unless told otherwise.
TRADING_VERSION = "1451"


def escape_request(data):
    """
    XML-escape every string in a request (element text and attribute values;
    not tag names). Listing calls send user-entered text, and ebaysdk's own
    escape_xml option only covers a string that is the whole content of an
    element, so it misses the values in a list (item specifics, picture
    URLs) and "#text" next to "@attrs".
    """
    if isinstance(data, dict):
        return {k: escape_request(v) for k, v in data.items()}
    if isinstance(data, list):
        return [escape_request(v) for v in data]
    if isinstance(data, str):
        return escape(data, {'"': "&quot;"})
    return data


def trading():
    """
    A Trading API connection for listing calls. Requests are escaped with
    escape_request() before they're sent, so ebaysdk's partial escaping is
    left off; they ask for a current API version rather than ebaysdk's
    default.
    """
    from fbc import app
    from ebaysdk.trading import Connection as Trading

    ebayconfig = app.config["EBAY_SETTINGS"]
    return Trading(
        debug=False,
        config_file=None,
        appid=ebayconfig["APP_ID"],
        domain="api.ebay.com",
        certid=ebayconfig["CERT_ID"],
        devid=ebayconfig["DEV_ID"],
        token=ebayconfig["USER_TOKEN"],
        compatibility=TRADING_VERSION,
    )


def listing_settings():
    from fbc import app

    return app.config["EBAY_SETTINGS"].get("LISTING", {})


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def summarize_response(resp):
    """
    Pull the parts of a Trading API response worth showing: errors and
    warnings (both arrive in Errors, told apart by SeverityCode) and any
    non-zero listing fees.
    """
    errors = []
    warnings = []
    for e in _as_list(resp.get("Errors")):
        text = e.get("LongMessage") or e.get("ShortMessage") or str(e)
        if e.get("SeverityCode") == "Warning":
            warnings.append(text)
        else:
            errors.append(text)
    fees = []
    fee_list = (resp.get("Fees") or {}).get("Fee")
    for f in _as_list(fee_list):
        amount = float((f.get("Fee") or {}).get("value", 0) or 0)
        if amount:
            fees.append({"name": f.get("Name"), "amount": amount})
    return {"errors": errors, "warnings": warnings, "fees": fees}


def _empty_summary():
    return {"errors": [], "warnings": [], "fees": []}


def _http_error(response):
    """A one-line description of an HTTP response eBay didn't answer properly."""
    if response is None:
        return "no response"
    status = getattr(response, "status_code", "?")
    reason = getattr(response, "reason", "") or ""
    text = getattr(response, "text", "") or ""
    if not isinstance(text, str):
        text = ""
    text = " ".join(text.split())[:300]
    return ("HTTP " + str(status) + " " + reason + (": " + text if text else "")).strip()


def _call(verb, data):
    """Run a Trading API call, returning (response dict or None, summary)."""
    from ebaysdk.exception import ConnectionError

    api = trading()
    try:
        response = api.execute(verb, escape_request(data))
        resp = response.dict()
        return resp, summarize_response(resp)
    except ConnectionError as e:
        logger.error(e)
        try:
            resp = e.response.dict() if e.response is not None else {}
        except Exception:
            resp = {}
        summary = summarize_response(resp)
        if not summary["errors"]:
            summary["errors"].append(str(e))
        return None, summary
    except Exception as e:
        # ebaysdk itself fails on a reply that isn't Trading API XML (an
        # HTTP error page, a retired call), so say what eBay actually sent.
        logger.exception(verb + " failed")
        summary = _empty_summary()
        summary["errors"].append(
            verb
            + " failed: "
            + _http_error(getattr(api, "response", None))
            + " ("
            + type(e).__name__
            + ")"
        )
        return None, summary


def check_ready(draft):
    """Problems fbc can spot before asking eBay; publishing needs none."""
    problems = []
    if not draft.picture_list():
        problems.append("Add at least one picture before publishing.")
    if draft.price is None:
        problems.append("Set a price.")
    settings = listing_settings()
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
    resp, summary = _call("GetItem", {"ItemID": item_id})
    if resp is None:
        summary["errors"].insert(
            0, "Couldn't read template listing " + str(item_id) + "."
        )
        return {}, summary
    return template_from_item(resp.get("Item") or {}), summary


def _listing_call(verb, draft):
    """Run AddFixedPriceItem or its Verify twin for `draft`."""
    settings = listing_settings()
    template, summary = template_fields(settings)
    if summary["errors"]:
        return None, summary
    return _call(verb, {"Item": build_item(draft, settings, template)})


def verify_draft(draft):
    """Have eBay check the listing without creating it."""
    _, summary = _listing_call("VerifyAddFixedPriceItem", draft)
    return summary


def publish_draft(draft):
    """
    List the draft on eBay. On success, record the EbayListing, link it to
    the Unit and mark the draft published. Returns the response summary.
    """
    from fbc import db
    from fbc.models.ebay import EbayListing

    problems = check_ready(draft)
    if problems:
        return {"errors": problems, "warnings": [], "fees": []}
    resp, summary = _listing_call("AddFixedPriceItem", draft)
    if resp is None or not resp.get("ItemID"):
        return summary
    item_id = resp["ItemID"]
    start_time = None
    if resp.get("StartTime"):
        start_time = datetime.datetime.strptime(
            resp["StartTime"], "%Y-%m-%dT%H:%M:%S.%fZ"
        )
    listing = db.session.query(EbayListing).filter_by(item_id=item_id).first()
    if not listing:
        listing = EbayListing(
            item_id=item_id,
            title=draft.title,
            price=draft.price,
            url="https://www.ebay.com/itm/" + item_id,
            start_time=start_time,
        )
        db.session.add(listing)
    draft.ebay_listing_id = item_id
    draft.status = "published"
    draft.unit.ebay_listing_id = item_id
    db.session.commit()
    logger.info("Published unit " + str(draft.unit_id) + " as " + item_id)
    return summary


OAUTH_AUTHORIZE_URL = "https://auth.ebay.com/oauth2/authorize"
OAUTH_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
MEDIA_URL = "https://apim.ebay.com/commerce/media/v1_beta/image/create_image_from_file"
MEDIA_SCOPE = "https://api.ebay.com/oauth/api_scope/sell.inventory"

# The current OAuth access token and when it stops working. Access tokens
# last two hours, so each worker keeps one rather than asking per upload.
_oauth = {"token": None, "expires": None}


class OAuthError(Exception):
    pass


def _remember_access_token(body, now):
    _oauth["token"] = body["access_token"]
    # Renew a minute early so a token never expires mid-request.
    _oauth["expires"] = now + datetime.timedelta(
        seconds=int(body.get("expires_in", 7200)) - 60
    )


def _token_request(data):
    """POST to eBay's OAuth token endpoint; the parsed reply, or OAuthError."""
    import requests
    from fbc import app

    ebayconfig = app.config["EBAY_SETTINGS"]
    r = requests.post(
        OAUTH_TOKEN_URL,
        auth=(ebayconfig["APP_ID"], ebayconfig["CERT_ID"]),
        data=data,
        timeout=30,
    )
    if r.status_code != 200:
        try:
            body = r.json()
            detail = body.get("error_description") or body.get("error") or str(body)
        except ValueError:
            detail = _http_error(r)
        raise OAuthError(detail)
    return r.json()


def consent_url(state):
    """
    The eBay sign-in page that asks the seller to let fbc manage their
    pictures. eBay then sends the browser to the RuName's "auth accepted"
    URL with a one-time code for exchange_code().
    """
    from urllib.parse import urlencode
    from fbc import app

    ebayconfig = app.config["EBAY_SETTINGS"]
    if not ebayconfig.get("RUNAME"):
        raise OAuthError(
            "Set EBAY_SETTINGS['RUNAME'] in config.py to your app's eBay"
            " Redirect URL name (RuName)."
        )
    return (
        OAUTH_AUTHORIZE_URL
        + "?"
        + urlencode(
            {
                "client_id": ebayconfig["APP_ID"],
                "redirect_uri": ebayconfig["RUNAME"],
                "response_type": "code",
                "scope": MEDIA_SCOPE,
                "state": state,
            }
        )
    )


def code_from(text):
    """
    The (code, state) in what eBay sent the browser back with: the whole
    address, just its query string, or the bare code. state is None when
    there isn't one.
    """
    from urllib.parse import parse_qs, unquote, urlparse

    text = (text or "").strip()
    if "code=" in text:
        query = urlparse(text).query if "?" in text else text
        params = parse_qs(query)
        code = (params.get("code") or [None])[0]
        state = (params.get("state") or [None])[0]
        return code, state
    return (unquote(text) or None), None


def exchange_code(code):
    """
    Swap a one-time sign-in code for tokens and save the refresh token.
    Returns the saved EbayOAuthToken; raises OAuthError if eBay says no.
    """
    from fbc import app, db
    from fbc.models.ebay import EbayOAuthToken

    now = datetime.datetime.utcnow()
    try:
        body = _token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": app.config["EBAY_SETTINGS"].get("RUNAME", ""),
            }
        )
    except OAuthError as e:
        raise OAuthError("eBay didn't accept the sign-in code: " + str(e))
    expires = None
    if body.get("refresh_token_expires_in"):
        expires = now + datetime.timedelta(
            seconds=int(body["refresh_token_expires_in"])
        )
    saved = EbayOAuthToken(
        refresh_token=body["refresh_token"], expires=expires, created=now
    )
    db.session.add(saved)
    db.session.commit()
    _remember_access_token(body, now)
    logger.info("Saved a new eBay OAuth refresh token, expires " + str(expires))
    return saved


def saved_oauth_token():
    """The newest EbayOAuthToken saved by Connect eBay, or None."""
    from fbc import db
    from fbc.models.ebay import EbayOAuthToken

    return db.session.query(EbayOAuthToken).order_by(EbayOAuthToken.id.desc()).first()


def oauth_token():
    """
    An OAuth user access token for eBay's REST APIs, minted from the refresh
    token Connect eBay saved (or EBAY_SETTINGS["OAUTH_REFRESH_TOKEN"]). The
    Trading API calls keep using the Auth'n'Auth USER_TOKEN; REST APIs
    don't accept it.
    """
    from fbc import app

    now = datetime.datetime.utcnow()
    if _oauth["token"] and _oauth["expires"] > now:
        return _oauth["token"]
    saved = saved_oauth_token()
    refresh_token = (
        saved.refresh_token
        if saved
        else app.config["EBAY_SETTINGS"].get("OAUTH_REFRESH_TOKEN")
    )
    if not refresh_token:
        raise OAuthError(
            "Connect fbc to eBay first (Sales > eBay Drafts > Connect eBay);"
            " picture uploads need it."
        )
    try:
        body = _token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": MEDIA_SCOPE,
            }
        )
    except OAuthError as e:
        raise OAuthError(
            "eBay refused the saved OAuth token (" + str(e) + "); connect"
            " fbc to eBay again under Sales > eBay Drafts > Connect eBay."
        )
    _remember_access_token(body, now)
    return _oauth["token"]


def _rest_errors(r):
    """Error messages from an eBay REST API error response."""
    try:
        errors = r.json().get("errors") or []
    except ValueError:
        errors = []
    messages = [e.get("longMessage") or e.get("message") or str(e) for e in errors]
    return messages or [_http_error(r)]


def upload_picture(fileobj, filename, content_type=None):
    """
    Upload one picture to eBay Picture Services through the Media API's
    createImageFromFile (Trading's UploadSiteHostedPictures was retired on
    2026-09-30). Returns (url, summary); url is None if the upload failed.
    """
    import requests

    summary = _empty_summary()
    try:
        token = oauth_token()
    except OAuthError as e:
        summary["errors"].append(str(e))
        return None, summary
    headers = {"Authorization": "Bearer " + token}
    try:
        r = requests.post(
            MEDIA_URL,
            headers=headers,
            files={"image": (filename, fileobj, content_type or "application/octet-stream")},
            timeout=120,
        )
    except requests.RequestException as e:
        logger.error(e)
        summary["errors"].append("Couldn't reach eBay to upload " + filename + ": " + str(e))
        return None, summary
    if r.status_code not in (200, 201):
        if r.status_code == 401:
            # A revoked token; fetch a new one next time.
            _oauth["token"] = None
        summary["errors"].extend(
            "Uploading " + filename + ": " + m for m in _rest_errors(r)
        )
        return None, summary
    try:
        url = r.json().get("imageUrl")
    except ValueError:
        url = None
    if not url and r.headers.get("Location"):
        # The image's details live at the Location the create call returns.
        image = requests.get(r.headers["Location"], headers=headers, timeout=30)
        if image.status_code == 200:
            url = image.json().get("imageUrl")
    if not url:
        summary["errors"].append(
            "eBay accepted " + filename + " but didn't say where it is: " + _http_error(r)
        )
    return url, summary


def seller_profiles():
    """
    The seller's business policies from GetUserPreferences, as
    ({"shipping"/"return"/"payment": [(id, name), ...]}, summary, details).
    `details` says what eBay sent back, for when the lists come back empty.
    """
    resp, summary = _call(
        "GetUserPreferences", {"ShowSellerProfilePreferences": "true"}
    )
    profiles = {"shipping": [], "return": [], "payment": []}
    if resp is None:
        return profiles, summary, {}
    logger.info("GetUserPreferences: " + json.dumps(resp, default=str))
    prefs = resp.get("SellerProfilePreferences")
    supported = ((prefs or {}).get("SupportedSellerProfiles") or {}).get(
        "SupportedSellerProfile"
    )
    details = {
        "ack": resp.get("Ack"),
        "returned_preferences": prefs is not None,
        "opted_in": (prefs or {}).get("SellerProfileOptedIn"),
        "profile_count": len(_as_list(supported)),
    }
    kinds = {"SHIPPING": "shipping", "RETURN_POLICY": "return", "PAYMENT": "payment"}
    for p in _as_list(supported):
        kind = kinds.get(p.get("ProfileType"))
        if kind:
            profiles[kind].append((p.get("ProfileID"), p.get("ProfileName")))
    return profiles, summary, details


def listing_details(item_id):
    """
    What an existing listing uses for shipping and returns, from GetItem:
    (business policies as {"shipping"/"return"/"payment": (id, name) or
    None}, the fields template_from_item() would copy, summary).
    """
    resp, summary = _call("GetItem", {"ItemID": item_id})
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
    settings = listing_settings()
    specifics = build_item_specifics(unit, settings.get("DEFAULT_SPECIFICS"))
    return EbayDraft(
        unit=unit,
        title=build_title(unit),
        price=unit.retail_price,
        best_offer=settings.get("BEST_OFFER", True),
        description=build_description(unit),
        item_specifics=format_specifics(specifics),
        weight_oz=default_weight(specifics, settings, disc_count(unit)),
        status="draft",
    )
