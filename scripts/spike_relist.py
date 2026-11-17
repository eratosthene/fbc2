"""
Throwaway spike for #208 step 4 (second version: the first asked for
VerifyRelistFixedPriceItem, which eBay says doesn't exist; this asks for
VerifyRelistItem, which may or may not accept a fixed price listing, and
that is part of what it finds out): does eBay accept a re-list of an ended
listing, and for how long after it ended? Read-only: for each ended listing
it makes two calls, GetItem (how it ended, and whether it was already
re-listed) and VerifyRelistItem, which has eBay check a re-list
without creating a listing or using an insertion. It never calls
RelistFixedPriceItem or any end, add or revise call (the script refuses to
send any other), saves nothing to the database or to a file, and prints no
tokens. Delete it once the Parked Listings re-list (#208 part B) is built.

It settles what the plan leaves open:
  - whether eBay accepts a re-list, and the error text when it doesn't;
  - how many days after ending a re-list stops being accepted (30 or 90?), so
    the EBAY_RELIST_DAYS setting can be set;
  - whether a free insertion discount shows in the fees of a re-list;
  - which ending reason a listing had (fbc's End and park sends NotAvailable)
    and whether each reason's listings are accepted.

It picks the ended listings fbc has, spread over the ages they have, parked
ones first. If fbc's oldest ended listing is younger than 90 days, the
window can't be settled from it, and the script says so.

Run it from the `scripts/` folder on a machine with the live config.py,
outside dev mode (dev mode refuses, on purpose):

    cd scripts
    pipenv run python spike_relist.py

Then paste the whole output as a comment on #208. It prints item ids, titles'
lengths and fees, never the access token or the refresh token. At most about
60 calls with the defaults. Options:

    --limit 30       how many ended listings to try (default 30)
    --ids 1,2,3      try exactly these ItemIDs instead
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, "..")
# Only the models and the database are needed (see "Writing scripts" in
# docs/development.md).
os.environ.setdefault("FBC_SKIP_VIEWS", "1")

from fbc import app, db
from fbc.core import devmode, localtime
from fbc.ebay import api
from fbc.models.ebay import EbayListing

# db.session needs an app context.
context = app.app_context()
context.push()

# The only calls this script may send. Anything else is a bug and stops it.
ALLOWED = {"GetItem", "VerifyRelistItem"}

calls = 0


def say(*parts):
    print(*parts, flush=True)


def call(verb, data):
    global calls
    if verb not in ALLOWED:
        raise SystemExit("Refusing to send " + verb + ": this spike only verifies.")
    calls += 1
    return api._call(verb, data)


def pick(limit):
    """Ended listings, parked first, thinned to `limit` spread over their ages."""
    rows = (
        db.session.query(EbayListing)
        .filter(EbayListing.ended.is_(True), EbayListing.end_time.isnot(None))
        .order_by(EbayListing.end_time)
        .all()
    )
    parked = [r for r in rows if r.parked_at]
    others = [r for r in rows if not r.parked_at]

    def spread(items, n):
        if len(items) <= n:
            return items
        step = (len(items) - 1) / (n - 1) if n > 1 else 0
        return [items[round(i * step)] for i in range(n)]

    # eBay stops answering for listings ended long ago, and fbc's end dates are
    # rough, so the most recent ones are always tried, then a spread of the rest.
    keep = spread(parked, limit)
    recent = others[-min(15, max(limit - len(keep), 0)) :] if others else []
    keep += recent
    older = others[: len(others) - len(recent)]
    keep += spread(older, max(limit - len(keep), 0))
    return sorted(keep, key=lambda r: r.parked_at or r.end_time)


def ended_when(row):
    return row.parked_at or row.end_time


def get_item(item_id):
    """(ending reason, end time, relisted as) from eBay, or an error text."""
    resp, summary = call(
        "GetItem",
        {
            "ItemID": item_id,
            "DetailLevel": "ReturnAll",
            "OutputSelector": ["ListingDetails", "SellingStatus"],
        },
    )
    if resp is None:
        return None, "; ".join(summary["errors"]) or "no answer"
    item = resp.get("Item") or {}
    details = item.get("ListingDetails") or {}
    return {
        "reason": details.get("EndingReason") or "(none given)",
        "end": details.get("EndTime"),
        "relisted_as": details.get("RelistedItemID"),
        "status": (item.get("SellingStatus") or {}).get("ListingStatus"),
    }, None


def real_end(info):
    """eBay's EndTime as a naive UTC datetime, or None."""
    if not info or not info.get("end"):
        return None
    try:
        return datetime.datetime.strptime(info["end"], "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        return None


def verify(item_id):
    """(accepted?, errors, warnings, fees) from VerifyRelistItem."""
    resp, summary = call("VerifyRelistItem", {"Item": {"ItemID": item_id}})
    accepted = resp is not None and not summary["errors"]
    return accepted, summary["errors"], summary["warnings"], summary


def fee_text(summary):
    """What eBay says it would charge, and any discount, in a line."""
    parts = []
    for fee in summary.get("fees") or []:
        text = "%s %.2f" % (fee["label"], fee["amount"])
        if fee["discount"]:
            text += " (discount %.2f, pay %.2f)" % (fee["discount"], fee["net"])
        parts.append(text)
    total = summary.get("fee_total")
    if total:
        parts.append(
            "total %.2f, discount %.2f, pay %.2f"
            % (total["amount"], total["discount"], total["net"])
        )
    return "; ".join(parts) or "no fees shown"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--ids", default="")
    args = parser.parse_args()

    if devmode.on():
        say("Dev mode is on; this must run against live. Nothing was sent.")
        return 1

    if args.ids:
        rows = [
            db.session.get(EbayListing, i.strip())
            for i in args.ids.split(",")
            if i.strip()
        ]
        rows = [r for r in rows if r is not None and ended_when(r)]
    else:
        rows = pick(args.limit)
    now = localtime.utcnow()
    say("Now (UTC):", now.isoformat(timespec="seconds"))
    say("Ended listings to check:", len(rows))
    if not rows:
        say("No ended listings in fbc to ask about.")
        return 1

    results = []
    for row in rows:
        days = (now - ended_when(row)).days
        say("")
        say(
            "== %s, ended %d days ago (%s), %s"
            % (
                row.item_id,
                days,
                "parked by End and park" if row.parked_at else "ended by a sync",
                "title %d characters" % len(row.title or ""),
            )
        )
        info, error = get_item(row.item_id)
        real = real_end(info)
        if real:
            # fbc's own end date is only "no later than"; eBay's is exact.
            days = (now - real).days
            say("   eBay's own end time puts it at %d days ago" % days)
        if info is None:
            say("   GetItem failed:", error)
            info = {"reason": "(unknown)", "end": None, "relisted_as": None}
        else:
            say(
                "   eBay: status %s, ending reason %s, ended %s, relisted as %s"
                % (info["status"], info["reason"], info["end"], info["relisted_as"])
            )
        accepted, errors, warnings, summary = verify(row.item_id)
        say("   re-list would be:", "ACCEPTED" if accepted else "REFUSED")
        for text in errors:
            say("   error:", text[:400])
        for text in warnings:
            say("   warning:", text[:400])
        if accepted:
            say("   fees:", fee_text(summary))
        results.append((days, accepted, info["reason"], errors))

    say("")
    say("== Summary")
    accepted = sorted(d for d, ok, _, _ in results if ok)
    refused = sorted(d for d, ok, _, _ in results if not ok)
    say("   accepted at ages (days):", accepted or "none")
    say("   refused at ages (days):", refused or "none")
    if accepted:
        say("   longest age still accepted: %d days" % accepted[-1])
        later = [d for d in refused if d > accepted[-1]]
        if later:
            say("   shortest age refused after that: %d days" % later[0])
        else:
            say(
                "   nothing older than that was refused, so the window is at least"
                " %d days; fbc has no older ended listing to test" % accepted[-1]
            )
    by_reason = {}
    for _, ok, reason, _ in results:
        by_reason.setdefault(reason, []).append(ok)
    for reason, oks in sorted(by_reason.items()):
        say(
            "   ending reason %s: %d accepted, %d refused"
            % (reason, oks.count(True), oks.count(False))
        )
    say("")
    say(json.dumps({"calls": calls}), "- nothing was saved, no listing was created.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
