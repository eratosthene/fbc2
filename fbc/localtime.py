"""
Shows times in the owner's timezone (the TIMEZONE setting, an IANA name such
as America/Chicago, which follows daylight saving; a fixed offset would be an
hour out for half the year).

fbc stores times as naive UTC, so everything here takes a naive UTC datetime
and the database is never changed. Typed `Date` columns (a receipt's, an
expense's) are dates the owner chose, not times, so they are not converted.
One format everywhere: `2026-11-13 5:45 PM`, or `2026-11-13` for a date.

`fbc/insertions.py` does its own Pacific arithmetic, as eBay's free insertion
month runs on Pacific time; this module only changes what is displayed.
"""

import datetime
import time
from zoneinfo import ZoneInfo

UTC = datetime.timezone.utc
DEFAULT = "America/Chicago"

_zone = ZoneInfo(DEFAULT)


def set_zone(name):
    """Use the IANA timezone `name`. Fails at startup on a name it can't load."""
    global _zone
    try:
        _zone = ZoneInfo(name or DEFAULT)
    except Exception as e:
        raise ValueError(
            "TIMEZONE must be an IANA timezone name such as America/Chicago, not "
            + repr(name)
        ) from e


def init_app(app):
    """Read TIMEZONE, add the template filters and show log times in it."""
    import logging

    set_zone(app.config.get("TIMEZONE"))
    app.add_template_filter(fmt_datetime, "localtime")
    app.add_template_filter(fmt_date, "localdate")
    # Log lines say when they happened in the same time as the pages.
    logging.Formatter.converter = staticmethod(_log_time)


def to_local(utc):
    """A naive UTC datetime as an aware one in the local zone."""
    return utc.replace(tzinfo=UTC).astimezone(_zone)


def local_naive(utc):
    """A naive UTC datetime as a naive local wall-clock one."""
    return to_local(utc).replace(tzinfo=None)


def to_utc(local):
    """A naive local wall-clock datetime as naive UTC. In the hour that daylight
    saving repeats, it takes the first."""
    return local.replace(tzinfo=_zone).astimezone(UTC).replace(tzinfo=None)


def fmt_datetime(utc):
    """`2026-11-13 5:45 PM` for a naive UTC datetime; blank for None."""
    if utc is None:
        return ""
    local = to_local(utc)
    return local.strftime("%Y-%m-%d ") + local.strftime("%I:%M %p").lstrip("0")


def fmt_date(utc):
    """The local date of a naive UTC datetime, `2026-11-13`; blank for None."""
    return "" if utc is None else to_local(utc).strftime("%Y-%m-%d")


def formatters(*columns):
    """A view's formatters_columns for these naive UTC DateTime columns."""
    return {c: fmt_datetime for c in columns}


def _log_time(seconds=None):
    when = datetime.datetime.fromtimestamp(
        time.time() if seconds is None else seconds, _zone
    )
    return when.timetuple()
