"""
Set the SKU (Custom label) on live eBay listings made before eBay Drafts.

Listings published from an eBay Draft carry `fbc-<unit id>` as their SKU.
Older ones don't, so the syncs can't tell which Unit they belong to.
`flask set-listing-skus` reads every active listing and its SKU from eBay,
works out the SKU each should have from the Unit linked to it, and, with
--apply, sets it with ReviseFixedPriceItem.

Everything goes through listing._call(), and so through listing.trading():
in dev mode it refuses, and nothing reaches eBay.
"""

import csv
import io
import logging
import time

from fbc import listing

logger = logging.getLogger()

PAGE_SIZE = 200
# Between revises, to stay well inside the Trading API's daily call limit.
PAUSE_SECONDS = 0.5

# What happens to a listing.
SET = "set"
RIGHT = "already right"
NO_UNIT = "no Unit linked"
SEVERAL = "several Units linked"
ONLY_SOLD = "only sold Units linked"
TYPED = "has a SKU typed on eBay"
CONFLICT = "SKU names another Unit"
FAILED = "eBay refused the change"

SKIP_REASONS = (NO_UNIT, SEVERAL, ONLY_SOLD, TYPED, CONFLICT, FAILED)


class EbayError(Exception):
    """eBay's active list couldn't be read."""


def active_listings(echo=None):
    """
    [(item id, title, SKU or "")] for every active listing, as the listings
    sync reads them: GetMyeBaySelling's ActiveList, a page at a time.
    """
    items = []
    page = 1
    pages = 1
    while page <= pages:
        resp, summary = listing._call(
            "GetMyeBaySelling",
            {
                "ActiveList": {
                    "Include": True,
                    "Sort": "StartTime",
                    "Pagination": {"EntriesPerPage": PAGE_SIZE, "PageNumber": page},
                }
            },
        )
        if resp is None or summary["errors"]:
            raise EbayError(
                "Couldn't read eBay's active listings: "
                + (" ".join(summary["errors"]) or "no response")
            )
        active = resp.get("ActiveList") or {}
        pages = int((active.get("PaginationResult") or {}).get("TotalNumberOfPages", 1))
        for item in listing._as_list((active.get("ItemArray") or {}).get("Item")):
            items.append(
                (str(item["ItemID"]), item.get("Title") or "", item.get("SKU") or "")
            )
        if echo:
            echo("Read page " + str(page) + " of " + str(pages) + ".")
        page += 1
    return items


def decide(item_id, current, units, overwrite=False):
    """
    (outcome, new SKU or None, detail) for one listing. `current` is its SKU
    on eBay and `units` every Unit whose ebay_listing_id is the item ID.
    """
    unsold = [u for u in units if not u.sold]
    if not units:
        return NO_UNIT, None, ""
    if not unsold:
        return ONLY_SOLD, None, ", ".join(str(u.id) for u in units)
    if len(unsold) > 1:
        return SEVERAL, None, ", ".join(str(u.id) for u in unsold)
    wanted = listing.sku(unsold[0].id)
    if current == wanted:
        return RIGHT, None, ""
    if not current:
        return SET, wanted, ""
    named = listing.unit_id_from_sku(current)
    if named is not None:
        return CONFLICT, None, current + " (wanted " + wanted + ")"
    if overwrite:
        return SET, wanted, "replaces " + current
    return TYPED, None, current + " (wanted " + wanted + ")"


def revise_sku(item_id, new_sku):
    """Set the SKU, sending nothing else. Returns the summary's errors."""
    resp, summary = listing._call(
        "ReviseFixedPriceItem", {"Item": {"ItemID": item_id, "SKU": new_sku}}
    )
    if resp is None and not summary["errors"]:
        summary["errors"].append("no response")
    return summary["errors"]


def set_skus(
    apply=False,
    overwrite=False,
    item=None,
    limit=None,
    echo=None,
    pause=PAUSE_SECONDS,
):
    """
    Work through the active listings. Returns {"counts": {outcome: n},
    "skipped": [(item id, title, reason, detail)]}. Without `apply` nothing is
    changed on eBay: "set" counts what would be.
    """
    from fbc import db
    from fbc.models.inventory import Unit

    echo = echo or (lambda text: None)
    items = active_listings(echo)
    if item:
        items = [i for i in items if i[0] == str(item)]
        if not items:
            raise EbayError("Item " + str(item) + " isn't an active listing.")

    counts = dict.fromkeys((SET, RIGHT) + SKIP_REASONS, 0)
    skipped = []
    calls = 0
    for item_id, title, current in items:
        units = db.session.query(Unit).filter(Unit.ebay_listing_id == item_id).all()
        outcome, new_sku, detail = decide(item_id, current, units, overwrite)
        if outcome == SET:
            if limit is not None and calls >= limit:
                continue
            calls += 1
            if apply:
                if calls > 1 and pause:
                    time.sleep(pause)
                errors = revise_sku(item_id, new_sku)
                if errors:
                    outcome, detail = FAILED, " ".join(errors)
            if outcome == FAILED:
                echo("Failed " + item_id + ": " + detail)
            else:
                echo(
                    ("Set " if apply else "Would set ")
                    + item_id
                    + " to "
                    + new_sku
                    + (" (" + detail + ")" if detail else "")
                )
        elif outcome != RIGHT:
            echo(
                "Skipped "
                + item_id
                + " ("
                + outcome
                + ")"
                + (": " + detail if detail else "")
            )
        counts[outcome] += 1
        if outcome in SKIP_REASONS:
            skipped.append((item_id, title, outcome, detail))
    return {"counts": counts, "skipped": skipped}


def skipped_csv(skipped):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["item_id", "title", "reason", "detail"])
    writer.writerows(skipped)
    return out.getvalue()
