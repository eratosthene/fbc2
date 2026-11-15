"""The calls that verify and publish an eBay Draft through the Trading API."""

import datetime
import logging


from fbc.ebay import api, drafts

logger = logging.getLogger()


def _listing_call(verb, draft):
    """Run AddFixedPriceItem or its Verify twin for `draft`."""
    settings = api.listing_settings()
    template, summary = drafts.template_fields(settings)
    if summary["errors"]:
        return None, summary
    return api._call(verb, {"Item": drafts.build_item(draft, settings, template)})


def verify_draft(draft):
    """
    Have eBay check the listing without creating it. Its fees are only
    estimates: eBay may leave out discounts such as free insertions.
    """
    _, summary = _listing_call("VerifyAddFixedPriceItem", draft)
    summary["source"] = "verify"
    return summary


def publish_draft(draft):
    """
    List the draft on eBay. On success, record the EbayListing, link it to
    the Unit and mark the draft published. Returns the response summary.
    """
    from fbc import db
    from fbc.models.ebay import EbayListing

    problems = drafts.check_ready(draft)
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
    summary["source"] = "publish"
    return summary


def end_listing(item_id):
    """
    End an active fixed price listing on eBay because the Unit sold
    elsewhere. Returns (True, message) when it's ended or eBay says it
    already was, or (False, why not).
    """
    resp, summary = api._call(
        "EndFixedPriceItem", {"ItemID": item_id, "EndingReason": "NotAvailable"}
    )
    if resp is not None and not summary["errors"]:
        logger.info("Ended eBay listing " + item_id)
        return True, "Ended on eBay."
    errors = "; ".join(summary["errors"]) or "eBay gave no answer."
    # eBay's error 1047: the listing has closed, which is what's wanted.
    if "already been closed" in errors or "already ended" in errors.lower():
        return True, "eBay says it had already ended."
    return False, errors
