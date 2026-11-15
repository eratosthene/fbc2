"""Calls to eBay's Trading API, and reading what comes back (fees, errors)."""

import json
import logging
import re
from xml.sax.saxutils import escape


from fbc.ebay import oauth

logger = logging.getLogger()


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


def trading(compatibility=TRADING_VERSION):
    """
    A Trading API connection, signed in with the OAuth token from Connect
    eBay (raises OAuthError if fbc isn't connected). Listing calls escape
    their requests with escape_request() before they're sent, so ebaysdk's
    partial escaping is left off; they ask for a current API version rather
    than ebaysdk's default, which compatibility=None keeps.
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
        iaf_token=oauth.oauth_token(oauth.TRADING_SCOPE),
        compatibility=compatibility,
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


def _amount(value):
    """The number in an ebaysdk amount ({"_currencyID": "USD", "value": "0.35"})."""
    if not value:
        return 0.0
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def _fee_label(name):
    """ "InsertionFee" -> "Insertion fee"."""
    words = re.findall(r"[A-Z][a-z]*|[a-z]+|\d+", name or "")
    if not words:
        return name or "Fee"
    return " ".join([words[0]] + [w.lower() for w in words[1:]])


# Calls whose Fee amounts already have PromotionalDiscount taken off. A
# free insertion comes back from VerifyAddFixedPriceItem as Fee 0.37 with
# discount 0.37, but from AddFixedPriceItem as Fee 0.0 with discount 0.37.
DISCOUNTED_FEE_VERBS = {"AddFixedPriceItem"}


def summarize_fees(fees, discounted=False):
    """
    (fees, total) from a response's Fees container, each fee with its full
    amount, any PromotionalDiscount eBay reports (such as a free
    insertion) and what's left to pay. `discounted` says the response's Fee
    amounts already have the discount taken off (see DISCOUNTED_FEE_VERBS).
    eBay's "ListingFee" is normally the sum of the other fees, and then
    it's the total rather than a fee of its own; if it isn't, it's listed
    like the rest and the total is their sum. Only fees or discounts that
    aren't zero are kept.
    """
    entries = []
    for f in _as_list((fees or {}).get("Fee")):
        fee = _amount(f.get("Fee"))
        discount = _amount(f.get("PromotionalDiscount"))
        if discounted:
            amount, net = round(fee + discount, 2), fee
        else:
            amount, net = fee, round(fee - discount, 2)
        entries.append(
            {
                "name": f.get("Name"),
                "label": _fee_label(f.get("Name")),
                "amount": amount,
                "discount": discount,
                "net": max(net, 0.0),
            }
        )
    others = [e for e in entries if e["name"] != "ListingFee"]
    listing_fee = next((e for e in entries if e["name"] == "ListingFee"), None)
    if (
        listing_fee
        and abs(listing_fee["amount"] - sum(e["amount"] for e in others)) < 0.005
    ):
        total = listing_fee
    else:
        others = entries
        total = None
    kept = [e for e in others if e["amount"] or e["discount"]]
    if total is None and kept:
        total = {
            "amount": round(sum(e["amount"] for e in kept), 2),
            "discount": round(sum(e["discount"] for e in kept), 2),
            "net": round(sum(e["net"] for e in kept), 2),
        }
    if total is None or not (total["amount"] or total["discount"]):
        return kept, None
    return kept, {k: total[k] for k in ("amount", "discount", "net")}


def summarize_response(resp, verb=None):
    """
    Pull the parts of a Trading API response worth showing: errors and
    warnings (both arrive in Errors, told apart by SeverityCode) and the
    listing fees (see summarize_fees). `verb` is the call it answers.
    """
    errors = []
    warnings = []
    for e in _as_list(resp.get("Errors")):
        text = e.get("LongMessage") or e.get("ShortMessage") or str(e)
        if e.get("SeverityCode") == "Warning":
            warnings.append(text)
        else:
            errors.append(text)
    if resp.get("Fees"):
        logger.info("eBay fees: " + json.dumps(resp["Fees"], default=str))
    fees, total = summarize_fees(resp.get("Fees"), verb in DISCOUNTED_FEE_VERBS)
    return {"errors": errors, "warnings": warnings, "fees": fees, "fee_total": total}


def _empty_summary():
    return {"errors": [], "warnings": [], "fees": []}


def _call(verb, data):
    """Run a Trading API call, returning (response dict or None, summary)."""
    from ebaysdk.exception import ConnectionError

    try:
        api = trading()
    except oauth.OAuthError as e:
        summary = _empty_summary()
        summary["errors"].append(str(e))
        return None, summary
    try:
        response = api.execute(verb, escape_request(data))
        resp = response.dict()
        return resp, summarize_response(resp, verb)
    except ConnectionError as e:
        logger.error(e)
        try:
            resp = e.response.dict() if e.response is not None else {}
        except Exception:
            resp = {}
        summary = summarize_response(resp, verb)
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
            + oauth._http_error(getattr(api, "response", None))
            + " ("
            + type(e).__name__
            + ")"
        )
        return None, summary


class ActiveListError(Exception):
    """eBay's active list couldn't be read in full."""


def fetch_active_items(page_size=200):
    """
    Every item in eBay's active list (GetMyeBaySelling, ActiveList), a page
    at a time. Raises ActiveListError unless every page came back and the
    items add up to the TotalNumberOfEntries eBay reports, so a caller can
    take an item missing from the result as no longer active.
    """
    items = {}
    total = None
    page = 1
    pages = 1
    while page <= pages:
        resp, summary = _call(
            "GetMyeBaySelling",
            {
                "ActiveList": {
                    "Include": True,
                    "Sort": "StartTime",
                    "Pagination": {"EntriesPerPage": page_size, "PageNumber": page},
                }
            },
        )
        if resp is None or summary["errors"]:
            raise ActiveListError(
                "Couldn't read page "
                + str(page)
                + " of eBay's active listings: "
                + (" ".join(summary["errors"]) or "no response")
            )
        active = resp.get("ActiveList") or {}
        paging = active.get("PaginationResult") or {}
        try:
            pages = int(paging["TotalNumberOfPages"])
            total = int(paging["TotalNumberOfEntries"])
        except (KeyError, TypeError, ValueError):
            raise ActiveListError(
                "eBay's reply for page " + str(page) + " had no listing count."
            )
        for item in _as_list((active.get("ItemArray") or {}).get("Item")):
            items[str(item["ItemID"])] = item
        page += 1
    if len(items) != total:
        raise ActiveListError(
            "Read "
            + str(len(items))
            + " active listings but eBay says there are "
            + str(total)
            + ". Run the sync again."
        )
    return list(items.values())


def item_category(item_id):
    """
    (eBay's category id for a listing, errors) from GetItem. The id is None
    when eBay couldn't say, with what went wrong in `errors`.
    """
    resp, summary = _call("GetItem", {"ItemID": item_id})
    if resp is None or summary["errors"]:
        return None, summary["errors"] or ["No response."]
    category = (resp.get("Item") or {}).get("PrimaryCategory") or {}
    category_id = category.get("CategoryID")
    if not category_id:
        return None, ["eBay's reply had no category."]
    return str(category_id), []


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
