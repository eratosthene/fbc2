"""
Throwaway spike for #208 step 3: what eBay's Sell Analytics API `getTrafficReport`
answers for per-listing views. Read-only: it only makes GET requests, and
saves nothing (no database writes, no files). Delete it once #208's
`fbc/ebay/traffic.py` exists.

It settles what the plan leaves open:
  - how many listing_ids one call takes (it tries 10, 100 and 300 by default);
  - the longest date_range (it tries 30 days up to 365);
  - how stale the numbers are (`lastUpdatedDate`, against now);
  - what comes back for a listing with no views, and for an id eBay doesn't know;
  - rate limit headers, and any 429;
  - the exact shape of one reply (printed once, cut to a few records).

Run it from the repo root, on a machine with the live config.py, in the
`scripts/` folder like the others, outside dev mode (dev mode refuses, on
purpose), after pressing Connect eBay again so the token has the
sell.analytics.readonly permission:

    cd scripts
    pipenv run python spike_traffic_report.py

Then paste the whole output from the terminal as a comment on #208. It prints listing ids,
titles' lengths and counts, never the access token or the refresh token. It
makes about a dozen calls. Options:

    --sizes 10,100,300       batch sizes to try
    --ranges 30,60,90,365    date_range lengths in days to try
"""

import argparse
import datetime
import json
import os
import sys
import time

sys.path.insert(0, "..")
# Only the models and the database are needed (see "Writing scripts" in
# docs/development.md).
os.environ.setdefault("FBC_SKIP_VIEWS", "1")
import requests

from fbc import app, db
from fbc.core import devmode, localtime
from fbc.ebay.oauth import ANALYTICS_SCOPE, OAuthError, _rest_errors, oauth_token
from fbc.models.ebay import EbayListing

# db.session needs an app context.
app.app_context().push()

URL = "https://api.ebay.com/sell/analytics/v1/traffic_report"
# Response headers worth showing; everything else is left out so that nothing
# sensitive is printed.
HEADERS_SHOWN = ("retry-after", "x-ebay-c-request-id")
# A listing id eBay can't know.
UNKNOWN_ID = "100000000000"

calls = 0


def say(*parts):
    print(*parts, flush=True)


def window(days):
    """date_range for the `days` days up to yesterday, UTC, as eBay writes it."""
    end = localtime.utcnow().date() - datetime.timedelta(days=1)
    start = end - datetime.timedelta(days=days - 1)
    return start.strftime("%Y%m%d") + ".." + end.strftime("%Y%m%d")


def report(token, ids, days):
    """One getTrafficReport call; (response or None, seconds it took)."""
    global calls
    calls += 1
    flt = "marketplace_ids:{EBAY_US},date_range:[" + window(days) + "]"
    if ids:
        flt += ",listing_ids:{" + "|".join(ids) + "}"
    started = time.time()
    try:
        r = requests.get(
            URL,
            headers={
                "Authorization": "Bearer " + token,
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            },
            params={
                "dimension": "LISTING",
                "metric": "LISTING_VIEWS_TOTAL",
                "filter": flt,
            },
            timeout=60,
        )
    except requests.RequestException as e:
        say("   request failed:", type(e).__name__)
        return None, time.time() - started
    return r, time.time() - started


def rate_headers(r):
    shown = {
        k: v
        for k, v in r.headers.items()
        if k.lower() in HEADERS_SHOWN or "ratelimit" in k.lower()
    }
    return shown or "none"


def parse(r):
    """(views by ItemID, the reply's other top-level fields)."""
    body = r.json()
    header = body.get("header") or {}
    dims = [d.get("key") for d in header.get("dimensionKeys") or []]
    metrics = [m.get("key") for m in header.get("metrics") or []]
    views = {}
    for rec in body.get("records") or []:
        item = (rec.get("dimensionValues") or [{}])[0].get("value")
        values = rec.get("metricValues") or []
        views[item] = values[0].get("value") if values else None
    return views, body, dims, metrics


def describe(label, r, seconds, asked):
    say("")
    say("== " + label)
    if r is None:
        return None
    say(
        "   HTTP", r.status_code, "in %.1fs" % seconds, "rate headers:", rate_headers(r)
    )
    if r.status_code != 200:
        say("   errors:", _rest_errors(r))
        return None
    views, body, dims, metrics = parse(r)
    say(
        "   asked for %d ids, got %d records; dimension keys %s, metrics %s"
        % (asked, len(views), dims, metrics)
    )
    say("   top-level fields:", sorted(body))
    for field in ("startDate", "endDate", "lastUpdatedDate"):
        say("   " + field + ":", body.get(field))
    if body.get("warnings"):
        say("   warnings:", json.dumps(body["warnings"])[:1500])
    return views, body


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--sizes", default="10,100,300")
    parser.add_argument("--ranges", default="30,60,90,365")
    args = parser.parse_args()
    sizes = [int(n) for n in args.sizes.split(",")]
    ranges = [int(n) for n in args.ranges.split(",")]

    if devmode.on():
        say("Dev mode is on; this must run against live. Nothing was sent.")
        return 1
    try:
        token = oauth_token(ANALYTICS_SCOPE)
    except OAuthError as e:
        say("Couldn't get a token with the analytics permission:", e)
        say("Press Connect eBay again (Sales > Connect eBay), then rerun.")
        return 1

    ids = [
        row.item_id
        for row in db.session.query(EbayListing.item_id)
        .filter(EbayListing.ended.is_(False))
        .order_by(EbayListing.item_id)
    ]
    say("Active listings in fbc:", len(ids))
    say("Now (UTC):", localtime.utcnow().isoformat(timespec="seconds"))
    if not ids:
        say("No active listings to ask about.")
        return 1

    # 1. Batch sizes, with the 30 days up to yesterday.
    first_body = None
    last_views = {}
    for n in sizes:
        batch = ids[:n]
        if len(batch) < n:
            say("")
            say(
                "== batch of %d: only %d active listings, so the most is tried"
                % (n, len(batch))
            )
        r, seconds = report(token, batch, 30)
        got = describe("batch of %d ids, 30 days" % len(batch), r, seconds, len(batch))
        if got:
            last_views, body = got
            first_body = first_body or body
            missing = [i for i in batch if i not in last_views]
            say("   asked-for ids missing from the reply:", len(missing))
            if missing:
                say("   first few missing:", missing[:5])
            extra = [i for i in last_views if i not in batch]
            say("   ids in the reply that weren't asked for:", len(extra))
        if r is not None and r.status_code == 429:
            say("Rate limited; stopping.")
            return 0

    # 2. What a count looks like: the spread, and zero or missing.
    if last_views:
        counts = sorted(int(v) for v in last_views.values() if v is not None)
        say("")
        say("== views over the last batch (30 days)")
        if counts:
            say(
                "   min %d, median %d, max %d; %d listings at 0; %d with no value"
                % (
                    counts[0],
                    counts[len(counts) // 2],
                    counts[-1],
                    counts.count(0),
                    len(last_views) - len(counts),
                )
            )

    # 3. An id eBay doesn't know, alone and among known ones.
    for label, batch in (
        ("an id eBay doesn't know, alone", [UNKNOWN_ID]),
        ("an unknown id among 2 known", [UNKNOWN_ID] + ids[:2]),
    ):
        r, seconds = report(token, batch, 30)
        got = describe(label, r, seconds, len(batch))
        if got:
            say("   records:", json.dumps(got[0]))

    # 4. The longest date_range. Stop at the first one eBay refuses.
    for days in ranges:
        r, seconds = report(token, ids[:10], days)
        got = describe("date_range of %d days, 10 ids" % days, r, seconds, 10)
        if got is None:
            say("   -> %d days was refused or failed; longer ones aren't tried" % days)
            break

    # 5. One raw reply, cut short so it stays readable.
    if first_body:
        shown = dict(first_body)
        records = shown.get("records") or []
        shown["records"] = records[:3]
        if len(records) > 3:
            shown["records_omitted"] = len(records) - 3
        say("")
        say("== one raw reply (first 3 records), no tokens in it")
        say(json.dumps(shown, indent=2)[:6000])

    say("")
    say("Calls made: %d. Nothing was saved." % calls)
    return 0


if __name__ == "__main__":
    sys.exit(main())
