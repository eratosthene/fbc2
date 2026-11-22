"""
Attach every `flask` command and group to the app's CLI. A command that isn't
listed here isn't there: tests/test_commands.py keeps the list.
"""

from fbc.commands import data, listings, syncing


def register(app):
    for command in (
        data.setup_db,
        data.combine_receipts,
        data.backfill_earnings,
        data.backfill_credits,
        data.split_descriptions,
        data.archive,
        data.sort_other_items,
        data.park_ended_listings,
        data.pictures_import,
        syncing.sync_group,
        syncing.scheduler_command,
        listings.set_listing_skus,
        listings.update_listing_descriptions,
    ):
        app.cli.add_command(command)
