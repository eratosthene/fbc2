"""Reading how many views each listing had, from eBay's Sell Analytics API
(getTrafficReport), for choosing which listings to end (see #208)."""

import datetime
import logging

from fbc.core import localtime

logger = logging.getLogger()


TRAFFIC_URL = "https://api.ebay.com/sell/analytics/v1/traffic_report"


# How many listings to ask about in one call. The spike on the live account
# got all of 193 back in one call; 100 keeps the address short and a
# failure small, and the pool is only a few hundred at most.
CHUNK = 100


# The views counted: this many days, ending yesterday. eBay allows up to 90.
WINDOW_DAYS = 30


# A count older than this is treated as unknown, since it no longer says how
# the listing is doing.
STALE_AFTER = datetime.timedelta(days=3)


class TrafficError(Exception):
    pass


def date_range(now):
    """
    The date_range filter for the WINDOW_DAYS days up to yesterday. eBay's
    days are Pacific, so yesterday is Pacific's; UTC can already be a day on.
    """
    from fbc.ebay.insertions import to_pacific

    end = to_pacific(now).date() - datetime.timedelta(days=1)
    start = end - datetime.timedelta(days=WINDOW_DAYS - 1)
    return start.strftime("%Y%m%d") + ".." + end.strftime("%Y%m%d")


def _chunk(item_ids):
    for i in range(0, len(item_ids), CHUNK):
        yield item_ids[i : i + CHUNK]


def _call(token, ids, now):
    import requests

    from fbc.ebay.oauth import _rest_errors, forget_access_token, ANALYTICS_SCOPE

    try:
        r = requests.get(
            TRAFFIC_URL,
            headers={
                "Authorization": "Bearer " + token,
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            },
            params={
                "dimension": "LISTING",
                "metric": "LISTING_VIEWS_TOTAL",
                "filter": "marketplace_ids:{EBAY_US},date_range:["
                + date_range(now)
                + "],listing_ids:{"
                + "|".join(ids)
                + "}",
            },
            timeout=60,
        )
    except requests.RequestException as e:
        logger.error(e)
        raise TrafficError("Couldn't reach eBay for listing views: " + str(e))
    if r.status_code == 200:
        return r.json()
    if r.status_code == 401:
        forget_access_token(ANALYTICS_SCOPE)
    if r.status_code == 429:
        raise TrafficError("eBay says too many requests for listing views; try later.")
    raise TrafficError("Getting listing views: " + "; ".join(_rest_errors(r)))


def _counts(body):
    """{ItemID: views} in a getTrafficReport reply. A listing eBay has no data
    for is left out, with a warning in the reply; a count that isn't a whole
    number is left out too, rather than read as 0."""
    found = {}
    for record in body.get("records") or []:
        item_id = ((record.get("dimensionValues") or [{}])[0]).get("value")
        values = record.get("metricValues") or [{}]
        count = values[0].get("value")
        if item_id and isinstance(count, int) and not isinstance(count, bool):
            found[str(item_id)] = count
    return found


def views(item_ids, now=None):
    """
    ({ItemID: views in the last 30 days}, [error messages]) for `item_ids`,
    in calls of CHUNK. An ItemID eBay has no data for is missing from the
    dict: unknown, not 0. A call that fails stops the rest, and the counts
    read before it are still returned with its message. Goes through
    oauth_token, so Dev Mode refuses it, as does a token saved before
    Connect eBay asked for the Analytics permission.
    """
    from fbc.ebay.oauth import ANALYTICS_SCOPE, OAuthError, oauth_token

    now = now or localtime.utcnow()
    found = {}
    ids = [str(i) for i in item_ids]
    if not ids:
        return found, []
    try:
        token = oauth_token(ANALYTICS_SCOPE)
    except OAuthError as e:
        return found, [str(e)]
    for chunk in _chunk(ids):
        try:
            found.update(
                {
                    k: v
                    for k, v in _counts(_call(token, chunk, now)).items()
                    if k in chunk
                }
            )
        except TrafficError as e:
            return found, [str(e)]
    return found, []


def save(counts, now=None):
    """Keep `counts` ({ItemID: views}) on their EbayListings, as of `now`.
    How many were saved."""
    from fbc import db
    from fbc.models.ebay import EbayListing

    now = now or localtime.utcnow()
    saved = 0
    for item_id, count in counts.items():
        listing = db.session.get(EbayListing, item_id)
        if listing is not None:
            listing.views_30d = count
            listing.views_checked = now
            saved += 1
    db.session.commit()
    return saved


def known(listing, now):
    """The listing's saved view count, or None when it's unknown: never
    counted, or counted more than STALE_AFTER ago."""
    if listing.views_30d is None or listing.views_checked is None:
        return None
    if now - listing.views_checked > STALE_AFTER:
        return None
    return listing.views_30d


def age_text(checked, now):
    """How long ago `checked` was, for the Draft page: "3 hours ago"."""
    seconds = (now - checked).total_seconds()
    if seconds < 3600:
        return "less than an hour ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return str(hours) + (" hour" if hours == 1 else " hours") + " ago"
    days = int(seconds // 86400)
    return str(days) + (" day" if days == 1 else " days") + " ago"


def recheck(listing, shown, now=None):
    """
    Ask eBay for one listing's views again, keep the answer, and say if it
    differs from `shown` (what the list showed: a count, or None for
    unknown). A sentence for the owner, or None when there's nothing to say.
    Never raises, and never blocks ending.
    """
    now = now or localtime.utcnow()
    counts, errors = views([listing.item_id], now)
    if errors:
        return "Couldn't re-read its views before ending it: " + errors[0]
    if listing.item_id not in counts:
        return "eBay had no views for it to re-read."
    save(counts, now)
    current = counts[listing.item_id]
    if shown is None:
        return (
            "Its views were unknown when the list was made; they are "
            + str(current)
            + " now."
        )
    if current != shown:
        return (
            "Its views changed since the list was made: "
            + str(shown)
            + " then, "
            + str(current)
            + " now."
        )
    return None
