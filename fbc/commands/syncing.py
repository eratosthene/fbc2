"""
The `flask sync` group and the scheduler. They are attached to `flask` by
fbc.commands.register.
"""

import logging

import click
from flask import current_app
from flask.cli import AppGroup, with_appcontext


@click.group("sync", cls=AppGroup)
def sync_group():
    """Run a Discogs or eBay sync, as its Sync button does."""


def _run_sync(job):
    """
    Run a fbc.sync.jobs Job in the foreground, printing its log. Exits 1 if it
    failed, logged an error (the syncs carry on from some) or is already
    running, from a button or another command.
    """
    from fbc.sync import state

    # fbc/__init__.py's logging.basicConfig prints every record on stderr already; the
    # job's own lines are the ones to show, so quiet those while it runs.
    root = logging.getLogger()
    quieted = [
        (h, h.level)
        for h in root.handlers
        if type(h) is logging.StreamHandler  # not the job's log handler
    ]
    for h, _ in quieted:
        h.setLevel(logging.CRITICAL + 1)
    try:
        result = state.run(job.kind, job.title, job.fn, echo=click.echo)
    except state.Busy as e:
        raise click.ClickException(str(e))
    finally:
        for h, level in quieted:
            h.setLevel(level)
    if result["status"] != "done":
        raise click.ClickException(job.title + " failed.")
    if result["errors"]:
        raise click.ClickException(
            job.title + " finished, but logged " + str(result["errors"]) + " errors."
        )


@sync_group.command("discogs")
def sync_discogs_command():
    """Sync the Discogs collection."""
    from fbc.sync import jobs

    _run_sync(jobs.discogs_job())


@sync_group.command("discogs-listings")
def sync_discogs_listings_command():
    """Sync the Discogs Marketplace listings (read only)."""
    from fbc.sync import jobs

    _run_sync(jobs.discogs_listings_job())


@sync_group.command("discogs-orders")
@click.option(
    "--deep", is_flag=True, help="Read every order, not just the last 30 days."
)
def sync_discogs_orders_command(deep):
    """Sync Discogs Marketplace orders (read only)."""
    from fbc.sync import jobs

    _run_sync(jobs.discogs_orders_job(deep=deep))


@sync_group.command("ebay-listings")
def sync_ebay_listings_command():
    """Sync eBay's active listings."""
    from fbc.sync import jobs

    _run_sync(jobs.ebay_listings_job())


@sync_group.command("ebay-views")
def sync_ebay_views_command():
    """Read how many views eBay's listings had (read only)."""
    from fbc.sync import jobs

    _run_sync(jobs.ebay_views_job())


@sync_group.command("ebay-orders")
@click.option("--update", is_flag=True, help="Also refresh the orders fbc already has.")
@click.option("--deep", is_flag=True, help="Look back 88 days, not 10.")
def sync_ebay_orders_command(update, deep):
    """Sync recent eBay orders."""
    from fbc.sync import jobs

    _run_sync(jobs.ebay_orders_job(update=update, deep=deep))


@click.command("scheduler")
@with_appcontext
def scheduler_command():
    """Run the syncs on their intervals, until stopped (see fbc/sync/scheduler.py)."""
    from fbc.sync import scheduler

    scheduler.loop(current_app.config)
