"""
How many of the month's free eBay insertions are used, and how many will be.

eBay's non-Store sellers get a set number of zero-insertion-fee listings a
month (EBAY_SETTINGS["LISTING"]["FREE_INSERTIONS"]). Each new listing, and
each monthly renewal of a Good 'Til Cancelled listing, uses one, and so does
a listing that was ended early. The assumptions below are eBay's help page
plus guesses; change them here, in count_insertions and month_bounds, and the
tests will say what moved:

- The month is a calendar month in US Pacific time (eBay.com's time).
- A renewal falls once a month, on the day of the month and the Pacific
  clock time the listing started (checked on three real listings against
  their Time Left; eBay's help says only "monthly"). A listing that started
  on the 29th to 31st is taken to renew on the month's last day when it has
  no such day, which is a guess. The listings sync also saves the date eBay's
  Time Left gives (renews_at); when it falls in the month, that date is used
  instead, so eBay's own answer wins.
- A relist is a new listing with a new StartTime, so it starts a new cycle
  (the sync keeps the new listing and drops the old one).
- A listing that ended is counted up to its end_time (see
  fbc/ebay/listings.py mark_ended_listings). Listings fbc never synced before they
  ended are missed, so the count can be a little low; entering the eBay
  app's number (calibrate) corrects that for the rest of the month.
- eBay has no call that says how many free listings are left; Seller Hub's
  counter is the check.
"""

import calendar
import datetime
import re

from fbc.core import localtime

# A GTC listing's Time Left is never more than a month.
MAX_TIME_LEFT_DAYS = 31
# Warn when the projected count is this close to the limit.
WARN_WITHIN = 10
DEFAULT_LIMIT = 250


def _nth_sunday(year, month, n):
    day = datetime.datetime(year, month, 1)
    day += datetime.timedelta(days=(6 - day.weekday()) % 7 + 7 * (n - 1))
    return day


def _is_pacific_dst(local):
    """Whether a Pacific wall-clock time is in daylight saving time."""
    start = _nth_sunday(local.year, 3, 2) + datetime.timedelta(hours=2)
    end = _nth_sunday(local.year, 11, 1) + datetime.timedelta(hours=2)
    return start <= local < end


def pacific_offset(utc):
    """Pacific's offset from UTC at a UTC time. (zoneinfo would need tzdata,
    which the Alpine image doesn't have.)"""
    # Daylight saving starts at 2:00 PST and ends at 2:00 PDT.
    start = _nth_sunday(utc.year, 3, 2) + datetime.timedelta(hours=10)
    end = _nth_sunday(utc.year, 11, 1) + datetime.timedelta(hours=9)
    if start <= utc < end:
        return datetime.timedelta(hours=-7)
    return datetime.timedelta(hours=-8)


def pacific_offset_local(local):
    """Pacific's offset from UTC at a Pacific wall-clock time."""
    return datetime.timedelta(hours=-7 if _is_pacific_dst(local) else -8)


def to_pacific(utc):
    return utc + pacific_offset(utc)


def month_bounds(now):
    """(start, end) in UTC of the Pacific calendar month containing `now`."""
    local = to_pacific(now)
    first = datetime.datetime(local.year, local.month, 1)
    following = datetime.datetime(
        local.year + (local.month == 12), local.month % 12 + 1, 1
    )

    def utc_of(local_time):
        offset = -7 if _is_pacific_dst(local_time) else -8
        return local_time - datetime.timedelta(hours=offset)

    return utc_of(first), utc_of(following)


_DURATION = re.compile(
    r"P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?$"
)


def renewal_time(item, fetched):
    """
    When a listing from eBay's active list next renews (UTC), or None. Uses
    its TimeLeft (an ISO 8601 duration such as "P29DT3H2M1S") from `fetched`,
    when the list was read; failing that, ListingDetails.EndTime. Anything
    more than a month away, such as a far-future placeholder, isn't a
    renewal date and gives None.
    """
    left = (item.get("TimeLeft") or "").strip()
    match = _DURATION.match(left) if left else None
    if match and any(match.groupdict().values()):
        parts = {k: int(v or 0) for k, v in match.groupdict().items()}
        when = fetched + datetime.timedelta(
            days=parts["d"], hours=parts["h"], minutes=parts["m"], seconds=parts["s"]
        )
    else:
        raw = (item.get("ListingDetails") or {}).get("EndTime")
        try:
            when = datetime.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S.000Z")
        except (TypeError, ValueError):
            return None
    if when > fetched + datetime.timedelta(days=MAX_TIME_LEFT_DAYS):
        return None
    return when


def _month_renewal(start, saved, now, month_start, month_end):
    """
    When a listing that started at `start` renews in the month of `now`: one
    renewal a month, on the start's day and Pacific time, or `saved` (eBay's
    own date, see renewal_time) when that falls in the month. Whether it has
    happened is for the caller to say.
    """
    local_now = to_pacific(now)
    days_in_month = calendar.monthrange(local_now.year, local_now.month)[1]
    local_start = to_pacific(start)
    local_renewal = local_start.replace(
        year=local_now.year,
        month=local_now.month,
        day=min(local_start.day, days_in_month),
    )
    renewal = local_renewal - pacific_offset_local(local_renewal)
    if saved and month_start <= saved < month_end:
        renewal = saved
    return renewal


def count_insertions(listings, now, limit=DEFAULT_LIMIT, adjustment=0):
    """
    This month's insertions, from `listings`: (start_time, end_time or None)
    pairs in UTC, end_time None for one still active, each optionally with a
    third item, the date it renews next (see renewal_time), used for the
    month's renewal when it falls in the month. Returns a dict with
    `used` (up to now), `due` (renewals still to come this month),
    `projected` (the two together), `limit`, and `near` and `over` for the
    projected count against the limit. `adjustment` is added to `used`: what
    the eBay app showed less fbc's own count, from calibrate().
    """
    month_start, month_end = month_bounds(now)
    used = due = 0
    for start, end, *more in listings:
        if start is None:
            continue
        saved = more[0] if more else None
        last = month_end if end is None else min(end, now)
        events = []
        # The start is an insertion of its own.
        if month_start <= start < month_end:
            events.append(start)
        renewal = _month_renewal(start, saved, now, month_start, month_end)
        if renewal > start:
            events.append(renewal)
        for t in events:
            if t <= last:
                if t <= now:
                    used += 1
                else:
                    due += 1
    used += adjustment
    projected = used + due
    return {
        "used": used,
        "due": due,
        "projected": projected,
        "limit": limit,
        "near": projected >= limit - WARN_WITHIN,
        "over": projected > limit,
    }


def free_insertions_limit():
    from fbc.ebay.api import listing_settings

    return int(listing_settings().get("FREE_INSERTIONS", DEFAULT_LIMIT))


def _listing_pairs(now):
    from fbc import db
    from fbc.models.ebay import EbayListing

    month_start, _ = month_bounds(now)
    rows = (
        db.session.query(
            EbayListing.start_time,
            EbayListing.ended,
            EbayListing.end_time,
            EbayListing.renews_at,
        )
        .filter(EbayListing.start_time.isnot(None))
        .filter(
            EbayListing.ended.is_(False)
            | (EbayListing.end_time.is_(None))
            | (EbayListing.end_time >= month_start)
        )
        .all()
    )
    return [
        (start, (end or start) if ended else None, renews)
        for start, ended, end, renews in rows
    ]


def listing_summary(now=None):
    """
    The home page's and Publish's view of the listings, from the database:
    active listings and the month's insertions (see count_insertions), and
    when the listings sync last finished. `synced` is None until it has, as
    nothing is marked ended before then and the counts would be wrong.
    `calibration` is this month's entry from the eBay app, or None.
    """
    from fbc import db
    from fbc.models.sync import SyncRecord
    from fbc.models.ebay import EbayListing, InsertionCalibration

    now = now or localtime.utcnow()
    synced = SyncRecord.last_finished(SyncRecord.LISTINGS_SYNC)
    month_start, _ = month_bounds(now)
    calibration = db.session.get(InsertionCalibration, month_start)
    summary = count_insertions(
        _listing_pairs(now),
        now,
        free_insertions_limit(),
        calibration.adjustment if calibration else 0,
    )
    summary["active"] = db.session.query(EbayListing).filter_by(ended=False).count()
    summary["synced"] = synced
    summary["calibration"] = None
    if calibration:
        summary["calibration"] = {
            "adjustment": calibration.adjustment,
            "remaining": calibration.remaining,
        }
    return summary


def calibrate(remaining, now=None):
    """
    Record the free insertions the eBay app shows as left, for this month,
    replacing any earlier entry. The adjustment is eBay's used count (limit
    less `remaining`) minus fbc's own at `now`; it stays for the month, and
    fbc's own count carries on from there. Run the listings sync first, so
    fbc's count is current. Returns the adjustment.
    """
    from fbc import db
    from fbc.models.ebay import InsertionCalibration

    now = now or localtime.utcnow()
    limit = free_insertions_limit()
    month_start, _ = month_bounds(now)
    own = count_insertions(_listing_pairs(now), now, limit)["used"]
    adjustment = (limit - remaining) - own
    row = db.session.get(InsertionCalibration, month_start)
    if row is None:
        row = InsertionCalibration(month_start=month_start)
        db.session.add(row)
    row.adjustment, row.remaining, row.entered = adjustment, remaining, now
    db.session.commit()
    return adjustment


def publish_warning(summary):
    """
    The warning to show before publishing one more listing, or None. It
    says nothing before the first listings sync, when the counts are unknown.
    """
    if not summary["synced"]:
        return None
    projected = summary["projected"] + 1
    if projected <= summary["limit"]:
        return None
    return (
        "Publishing this would make "
        + str(projected)
        + " insertions this month, past the "
        + str(summary["limit"])
        + " free ones. eBay charges an insertion fee for each one over."
    )


def uncovered_insertion_fee(result):
    """
    The insertion fee eBay still charged in a Check with eBay result (the
    draft's saved messages), or None. A free insertion comes back with a
    PromotionalDiscount that cancels the fee, so a fee left over means the
    free ones are used up.
    """
    if not result or result.get("source") != "verify":
        return None
    for fee in result.get("fees") or []:
        if fee.get("name") == "InsertionFee" and fee.get("net", 0) > 0:
            return fee["net"]
    return None


# How many listings to offer to end when Publish would go over the limit.
END_CHOICES = 5


def _end_pool(now):
    """
    The active listings that ending would save an insertion on: those whose
    renewal is still to come this month (the same renewal count_insertions
    uses). Each is a dict with the listing, its Unit (or None), `renews`, and
    `views`, which is None, meaning unknown, until the views step (#208)
    brings counts. Left out: mystery boxes, Other Items, archived and ended
    listings, a listing with more than one Unit, and one whose Unit has sold.
    """
    from fbc import db
    from fbc.models.ebay import EbayListing
    from fbc.models.inventory import Unit
    from fbc.models.other import OtherItem

    month_start, month_end = month_bounds(now)
    other = {
        r[0]
        for r in db.session.query(OtherItem.ebay_listing_id).filter(
            OtherItem.ebay_listing_id.isnot(None)
        )
    }
    units = {}
    for unit in db.session.query(Unit).filter(Unit.ebay_listing_id.isnot(None)):
        units.setdefault(unit.ebay_listing_id, []).append(unit)
    pool = []
    listings = (
        db.session.query(EbayListing)
        .filter(
            EbayListing.ended.is_(False),
            EbayListing.archived.is_(False),
            EbayListing.start_time.isnot(None),
        )
        .all()
    )
    for listing in listings:
        if listing.is_mystery_box() or listing.item_id in other:
            continue
        on_it = units.get(listing.item_id, [])
        if len(on_it) > 1 or any(u.sold for u in on_it):
            continue
        renews = _month_renewal(
            listing.start_time, listing.renews_at, now, month_start, month_end
        )
        if renews <= now or renews <= listing.start_time:
            continue
        pool.append(
            {
                "listing": listing,
                "unit": on_it[0] if on_it else None,
                "renews": renews,
                "views": None,
            }
        )
    return pool


def end_candidates(now=None, count=END_CHOICES):
    """
    Up to `count` listings to offer to end to make room, each a dict (see
    _end_pool). Fewest views first, ties to the sooner renewal, unknown views
    after every known count and never read as 0. Until views are built they
    are all unknown, so this is the soonest renewals.
    """
    now = now or localtime.utcnow()
    pool = _end_pool(now)
    pool.sort(
        key=lambda c: (
            c["views"] is None,
            c["views"] or 0,
            c["renews"],
            c["listing"].item_id,
        )
    )
    return pool[:count]
