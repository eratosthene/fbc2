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

COUNTRIES = {
    "US": "United States",
    "UK": "United Kingdom",
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
            put("Era", decade(release.year))
        genres = []
        for g in release.genres:
            genres.extend(GENRES.get(g.name, [g.name]))
        put("Genre", *genres)
        put("Style", decade(release.year), *[s.name for s in release.styles])
        if release.country:
            put("Country of Origin", COUNTRIES.get(release.country, release.country))

    descriptions, text = _format_descriptions(release)
    unit_type = unit.unit_type or ""
    put("Type", *[t for t in TYPES if t in descriptions])
    put("Type", *[t for t in TYPES if t in unit_type.split()])
    put("Record Size", *[s for s in SIZES if s in descriptions])
    put("Record Size", *[s for s in SIZES if unit_type.startswith(s)])
    for d in descriptions:
        m = re.match(r"(\d+)", d)
        if m and "RPM" in d:
            put("Speed", m.group(1) + " RPM")
    if "LP" in specifics.get("Type", []):
        put("Speed", "33 RPM")
    put("Number of Audio Channels", *[c for c in CHANNELS if c in descriptions])
    put("Color", *[c for c in COLORS if c.lower() in text.lower()])

    put("Vinyl Matrix Number", unit.matrix)
    put("Edition", unit.pressing)
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
    "Spyro Gyra - Carnaval 1980 First Edition Vinyl LP Fusion Jazz-Funk VG+",
    dropping styles and then the edition until it fits in 80 characters.
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
    unit_type = unit.unit_type or ""
    kind = next((t for t in TYPES if t in unit_type.split()), "")
    record, _ = split_grading(unit.grading)
    edition = unit.pressing or ""

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


def build_item(draft, settings):
    """
    The Item container for AddFixedPriceItem / VerifyAddFixedPriceItem.
    `settings` is EBAY_SETTINGS["LISTING"] from config.py.
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
        "SellerProfiles": {
            "SellerShippingProfile": {
                "ShippingProfileID": settings.get("SHIPPING_PROFILE_ID", "")
            },
            "SellerReturnProfile": {
                "ReturnProfileID": settings.get("RETURN_PROFILE_ID", "")
            },
            "SellerPaymentProfile": {
                "PaymentProfileID": settings.get("PAYMENT_PROFILE_ID", "")
            },
        },
    }
    if settings.get("POSTAL_CODE"):
        item["PostalCode"] = settings["POSTAL_CODE"]
    if settings.get("LOCATION"):
        item["Location"] = settings["LOCATION"]
    if draft.best_offer:
        item["BestOfferDetails"] = {"BestOfferEnabled": "true"}
    pictures = draft.picture_list()
    if pictures:
        item["PictureDetails"] = {"PictureURL": pictures}
    return item


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


def trading():
    """
    A Trading API connection for listing calls. Unlike the sync calls these
    send user-entered text, so XML escaping is turned on.
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
        escape_xml=True,
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


def _call(verb, data):
    """Run a Trading API call, returning (response dict or None, summary)."""
    from ebaysdk.exception import ConnectionError

    api = trading()
    try:
        response = api.execute(verb, data)
        resp = response.dict()
        return resp, summarize_response(resp)
    except ConnectionError as e:
        logger.error(e)
        resp = e.response.dict() if e.response is not None else {}
        summary = summarize_response(resp)
        if not summary["errors"]:
            summary["errors"].append(str(e))
        return None, summary


def check_ready(draft):
    """Problems fbc can spot before asking eBay; publishing needs none."""
    problems = []
    if not draft.picture_list():
        problems.append("Add at least one picture before publishing.")
    if draft.price is None:
        problems.append("Set a price.")
    settings = listing_settings()
    for key in ("SHIPPING_PROFILE_ID", "RETURN_PROFILE_ID", "PAYMENT_PROFILE_ID"):
        if not settings.get(key):
            problems.append(
                "Set EBAY_SETTINGS['LISTING']['" + key + "'] in config.py."
            )
    return problems


def verify_draft(draft):
    """Have eBay check the listing without creating it."""
    _, summary = _call(
        "VerifyAddFixedPriceItem", {"Item": build_item(draft, listing_settings())}
    )
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
    resp, summary = _call(
        "AddFixedPriceItem", {"Item": build_item(draft, listing_settings())}
    )
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


def upload_picture(fileobj, filename):
    """
    Upload one picture to eBay Picture Services. Returns (url, summary);
    url is None if the upload failed. Unused uploads expire after 30 days.
    """
    from ebaysdk.exception import ConnectionError

    api = trading()
    try:
        response = api.execute(
            "UploadSiteHostedPictures",
            {"PictureName": filename, "PictureSet": "Supersize", "ExtensionInDays": 30},
            files={"file": (filename, fileobj)},
        )
        resp = response.dict()
        return resp["SiteHostedPictureDetails"]["FullURL"], summarize_response(resp)
    except ConnectionError as e:
        logger.error(e)
        resp = e.response.dict() if e.response is not None else {}
        summary = summarize_response(resp)
        if not summary["errors"]:
            summary["errors"].append(str(e))
        return None, summary


def seller_profiles():
    """
    The seller's business policies, as {"shipping"/"return"/"payment": [(id,
    name), ...]}, so their IDs can be copied into config.py.
    """
    resp, summary = _call(
        "GetUserPreferences", {"ShowSellerProfilePreferences": "true"}
    )
    profiles = {"shipping": [], "return": [], "payment": []}
    if resp is None:
        return profiles, summary
    prefs = resp.get("SellerProfilePreferences") or {}
    supported = (prefs.get("SupportedSellerProfiles") or {}).get(
        "SupportedSellerProfile"
    )
    kinds = {"SHIPPING": "shipping", "RETURN_POLICY": "return", "PAYMENT": "payment"}
    for p in _as_list(supported):
        kind = kinds.get(p.get("ProfileType"))
        if kind:
            profiles[kind].append((p.get("ProfileID"), p.get("ProfileName")))
    return profiles, summary


def new_draft(unit):
    """A fresh EbayDraft for `unit`, filled in from the Unit and Discogs."""
    from fbc.models.ebay import EbayDraft

    ensure_release_details(unit.discogs_release)
    settings = listing_settings()
    return EbayDraft(
        unit=unit,
        title=build_title(unit),
        price=unit.retail_price,
        best_offer=settings.get("BEST_OFFER", True),
        description=build_description(unit),
        item_specifics=format_specifics(
            build_item_specifics(unit, settings.get("DEFAULT_SPECIFICS"))
        ),
        status="draft",
    )
