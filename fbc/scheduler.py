"""
Keeps the Discogs and eBay syncs fresh by running each one when it's due.

`flask scheduler` is a loop that wakes every TICK_SECONDS, asks what is due
and runs those syncs one at a time, in this process, with the same jobs the
Sync buttons and `flask sync` run (see fbc.syncjobs). docker/docker-entrypoint.sh
starts it in the same container as gunicorn, because the job lock lives in
that container's job files and PIDs (see fbc.sync).

A sync is due when it has never finished, or last finished at least its
interval ago (sync.last_finished, so a sync run by hand counts too). Two
things stop it hammering eBay or Discogs:

- A sync that is already running, from a button or a command, is skipped and
  tried again on the next tick. That isn't a failure.
- A sync that fails records nothing, so it would be due again at once. It
  waits RETRY_MINUTES after a failure instead. (Kept in memory: a restart
  forgets it, which only means one more try.)

In dev mode it only logs that syncs are off and exits, so nothing reaches eBay
or Discogs.
"""

import datetime
import logging
import time
from collections import namedtuple

from fbc import db, devmode, sync, syncjobs

logger = logging.getLogger()

TICK_SECONDS = 60

# The settings, each with its default. Minutes between syncs, by sync_record
# name. Orders are checked more often than listings because a new order is
# something to ship; Discogs changes least.
DEFAULT_INTERVALS = {
    syncjobs.ORDERS_SYNC: 15,
    sync.LISTINGS_SYNC: 30,
    syncjobs.DISCOGS_SYNC: 120,
    syncjobs.DISCOGS_LISTINGS_SYNC: 30,
}
INTERVAL_SETTINGS = {
    syncjobs.ORDERS_SYNC: "SCHEDULER_EBAY_ORDERS_MINUTES",
    sync.LISTINGS_SYNC: "SCHEDULER_EBAY_LISTINGS_MINUTES",
    syncjobs.DISCOGS_SYNC: "SCHEDULER_DISCOGS_MINUTES",
    syncjobs.DISCOGS_LISTINGS_SYNC: "SCHEDULER_DISCOGS_LISTINGS_MINUTES",
}
DEFAULT_RETRY_MINUTES = 10

# The jobs, in the order they run when several are due: orders first, since
# they're the most urgent.
JOBS = {
    syncjobs.ORDERS_SYNC: syncjobs.ebay_orders_job,
    sync.LISTINGS_SYNC: syncjobs.ebay_listings_job,
    syncjobs.DISCOGS_SYNC: syncjobs.discogs_job,
    syncjobs.DISCOGS_LISTINGS_SYNC: syncjobs.discogs_listings_job,
}

Schedule = namedtuple("Schedule", "intervals retry")


def enabled(config):
    """True unless SCHEDULER is False in config.py. Dev mode is checked apart."""
    return bool(config.get("SCHEDULER", True))


def read_schedule(config):
    """The intervals and retry delay from the settings, as timedeltas."""

    def minutes(key, default):
        try:
            value = float(config.get(key, default))
        except (TypeError, ValueError):
            logger.error("%s must be a number of minutes; using %s.", key, default)
            value = default
        return datetime.timedelta(minutes=max(value, 1))

    intervals = {
        name: minutes(INTERVAL_SETTINGS[name], DEFAULT_INTERVALS[name]) for name in JOBS
    }
    return Schedule(
        intervals, minutes("SCHEDULER_RETRY_MINUTES", DEFAULT_RETRY_MINUTES)
    )


def due(schedule, failed_at, now):
    """The names of the syncs that are due at `now` (naive UTC), in run order."""
    # The syncs record their finish from their own app context, so a session
    # that is still in its first transaction would never see it (MariaDB's
    # default isolation shows a transaction one snapshot), and every sync would
    # look due on every tick. Start each look from a fresh session.
    db.session.remove()
    names = []
    for name in JOBS:
        last = sync.last_finished(name)
        if last is not None and now - last < schedule.intervals[name]:
            continue
        failed = failed_at.get(name)
        if failed is not None and now - failed < schedule.retry:
            continue
        names.append(name)
    return names


def run_due(schedule, failed_at, now=None):
    """
    Run every sync that is due, one after another, and return {name: outcome}
    where outcome is "done", "failed" or "busy". `failed_at` is the dict that
    remembers when each sync last failed; it's updated here.
    """
    now = now or sync.utcnow()
    outcomes = {}
    for name in due(schedule, failed_at, now):
        job = JOBS[name]()
        # No echo: the root logger already prints each line to the container's
        # output, and echoing it too would print every line twice.
        try:
            result = sync.run(job.kind, job.title, job.fn, echo=None)
        except sync.Busy:
            logger.info("%s is already running; trying again next time.", job.title)
            outcomes[name] = "busy"
            continue
        # A sync can also log an error and return without recording that it
        # finished (eBay not connected, a call that failed), which is a
        # failure as far as being due again goes. One that logged errors but
        # still recorded its finish isn't due until its interval is up, so
        # the retry delay changes nothing for it.
        if result["status"] == "done" and not result["errors"]:
            failed_at.pop(name, None)
            outcomes[name] = "done"
        else:
            failed_at[name] = sync.utcnow()
            outcomes[name] = "failed"
    return outcomes


def loop(config, sleep=time.sleep):
    """Run forever, or return at once if the scheduler is off. Never raises."""
    if devmode.on():
        logger.info(devmode.message("scheduled syncs") + " Not running.")
        return
    if not enabled(config):
        logger.info("SCHEDULER is False in config.py; scheduled syncs are off.")
        return
    schedule = read_schedule(config)
    logger.info(
        "Scheduler started. Minutes between syncs: "
        + ", ".join(
            "%s %g" % (n, i.total_seconds() / 60) for n, i in schedule.intervals.items()
        )
        + "."
    )
    failed_at = {}
    while True:
        try:
            run_due(schedule, failed_at)
        except Exception:
            # Say so and carry on: one bad tick shouldn't end the scheduler.
            logger.exception("The scheduler's tick failed")
        sleep(TICK_SECONDS)
