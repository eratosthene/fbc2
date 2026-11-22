"""Every `flask` command is still attached (fbc/commands/register.py)."""

import fbc

COMMANDS = {
    "setup-db",
    "combine-receipts",
    "backfill-earnings",
    "backfill-credits",
    "split-descriptions",
    "archive",
    "sort-other-items",
    "park-ended-listings",
    "pictures-import",
    "sync",
    "scheduler",
    "set-listing-skus",
    "update-listing-descriptions",
}

SYNC_COMMANDS = {
    "discogs",
    "discogs-listings",
    "discogs-orders",
    "ebay-listings",
    "ebay-orders",
    "ebay-views",
}


def test_every_fbc_command_is_registered():
    assert COMMANDS <= set(fbc.app.cli.commands)


def test_the_sync_group_has_every_sync():
    assert set(fbc.app.cli.commands["sync"].commands) == SYNC_COMMANDS


def test_every_command_in_SKIP_VIEWS_COMMANDS_exists():
    assert fbc.SKIP_VIEWS_COMMANDS <= COMMANDS
