"""
When an ended eBay listing can still be re-listed (see #208). The window is
90 days, as eBay's API docs say; a spike on the live account had eBay accept
re-lists up to 83 days after they ended (the oldest it found that still
existed), so the owner's belief of 30 days was wrong. It stays a setting,
EBAY_RELIST_DAYS, in case eBay changes it. The dates are UTC, as the listings
are.
"""

import datetime

from flask import current_app

from fbc.core import localtime

DEFAULT_DAYS = 90
# A listing this close to its re-list by date gets a "re-list soon" marker.
SOON_DAYS = 7


def window_days():
    """The re-list window in days, from config.py, or DEFAULT_DAYS if it is
    missing or isn't a positive number."""
    days = current_app.config.get("EBAY_RELIST_DAYS")
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        return DEFAULT_DAYS
    return days


def relist_by(ended_at):
    """The last moment (UTC) a listing ended at `ended_at` can be re-listed,
    or None if it has no end date."""
    if ended_at is None:
        return None
    return ended_at + datetime.timedelta(days=window_days())


def days_out(ended_at, now=None):
    """Whole days since `ended_at`, or None."""
    if ended_at is None:
        return None
    return max(((now or localtime.utcnow()) - ended_at).days, 0)


def is_soon(ended_at, now=None):
    """Whether the re-list by date is within SOON_DAYS days, or has passed."""
    by = relist_by(ended_at)
    if by is None:
        return False
    return by - (now or localtime.utcnow()) <= datetime.timedelta(days=SOON_DAYS)
