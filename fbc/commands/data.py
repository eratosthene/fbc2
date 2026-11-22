"""
Commands that fix or fill in data, and set up the database. They are
attached to `flask` by fbc.commands.register.
"""

import click
import sqlalchemy as sa
from flask.cli import with_appcontext
from flask_migrate import stamp, upgrade


@click.command("setup-db")
@with_appcontext
def setup_db():
    """Create a new database from the models, or migrate an existing one."""
    from fbc import SKIP_VIEWS, db

    if SKIP_VIEWS:
        raise click.ClickException(
            "Run this without FBC_SKIP_VIEWS: it needs Flask-AppBuilder's tables."
        )
    # Importing the app has already created Flask-AppBuilder's ab_* tables, so
    # look for our own. A database from before migrations were added has
    # `unit` but no `alembic_version`, and still needs every migration.
    tables = sa.inspect(db.engine).get_table_names()
    if "alembic_version" in tables or "unit" in tables:
        upgrade()
    else:
        # The models match the migrations' head, so build the schema from them
        # and record it as being at head (Alembic's recipe for new databases).
        db.create_all()
        stamp()
        click.echo("Created a new database at the latest migration.")


@click.command("combine-receipts")
@click.option("--apply", is_flag=True, help="Save the changes; without it, only show.")
@click.option(
    "--receipts",
    help="Combine just these Sales Receipts, by id (e.g. 516,517), into one.",
)
@with_appcontext
def combine_receipts(apply, receipts):
    """Combine Sales Receipts that stand for one shipment."""
    from fbc.sales.combine import combine_all

    only = [int(i) for i in receipts.split(",")] if receipts else None
    count = combine_all(apply=apply, only=only, echo=click.echo)
    click.echo(("Combined " if apply else "Would combine ") + str(count) + " groups.")


@click.command("backfill-earnings")
@click.option("--apply", is_flag=True, help="Save the lines; without it, only show.")
@click.option(
    "--update-net",
    is_flag=True,
    help="Set Net Sold to eBay's total where they differ, instead of keeping"
    " the difference as a manual adjustment.",
)
@click.option(
    "--since", type=click.DateTime(["%Y-%m-%d"]), help="Only receipts from this date."
)
@click.option("--limit", type=int, help="At most this many receipts, newest first.")
@with_appcontext
def backfill_earnings(apply, update_net, since, limit):
    """Give past Sales Receipts their earnings lines from eBay."""
    from fbc.ebay.finances import backfill_earnings

    counts = backfill_earnings(
        apply=apply,
        update_net=update_net,
        since=since.date() if since else None,
        limit=limit,
        echo=click.echo,
    )
    click.echo(
        "Saved "
        + str(counts["saved"])
        + ", Net Sold differed on "
        + str(counts["differ"])
        + ", eBay had no sale for "
        + str(counts["skipped"])
        + ", order on other receipts too for "
        + str(counts["shared"])
        + ", return or dispute open for "
        + str(counts["unsettled"])
        + "."
    )


@click.command("backfill-credits")
@click.option("--apply", is_flag=True, help="Save the credits; without it, only show.")
@click.option(
    "--overwrite",
    is_flag=True,
    help="Also read again the releases that have credits, and replace them."
    " Don't combine with --limit: each run starts from the first release.",
)
@click.option("--limit", type=int, help="Fetch at most this many releases.")
@with_appcontext
def backfill_credits(apply, overwrite, limit):
    """Give classical releases their composer and conductor credits."""
    from fbc.ebay.drafts import backfill_credits

    counts = backfill_credits(
        apply=apply, limit=limit, overwrite=overwrite, echo=click.echo
    )
    click.echo(
        str(counts["found"])
        + (
            " classical releases to read. Saved "
            if overwrite
            else " classical releases have no credits. Saved "
        )
        + str(counts["saved"])
        + ", Discogs couldn't give "
        + str(counts["failed"])
        + "."
    )
    if not apply:
        click.echo("This was a dry run. Add --apply to fetch them.")


@click.command("split-descriptions")
@click.option("--apply", is_flag=True, help="Save the notes; without it, only show.")
@with_appcontext
def split_descriptions(apply):
    """Split Units' old descriptions into Record, Sleeve and Dust Jacket Notes."""
    from fbc.ebay.splitting import split_all

    counts = split_all(apply=apply, echo=click.echo)
    click.echo(
        ("Split " if apply else "Would split ")
        + str(counts["split"])
        + " descriptions. Left alone because they don't split cleanly: "
        + str(counts["problems"])
        + ", because they have notes already: "
        + str(counts["skipped"])
        + "."
    )
    if not apply:
        click.echo("This was a dry run. Add --apply to save the notes.")


@click.command("archive")
@click.option("--apply", is_flag=True, help="Save the changes; without it, only show.")
@with_appcontext
def archive(apply):
    """Archive old, finished Units, listings, orders and receipts."""
    from fbc.sales.archive import archive_all

    archive_all(apply=apply, echo=click.echo)
    if not apply:
        click.echo("This was a dry run. Add --apply to archive them.")


@click.command("sort-other-items")
@click.option("--apply", is_flag=True, help="Save the changes; without it, only show.")
@click.option(
    "--limit", type=int, help="Read the category of at most this many listings."
)
@with_appcontext
def sort_other_items(apply, limit):
    """Make Other Items of the old eBay listings that aren't vinyl records."""
    from fbc.ebay import sorting

    counts = sorting.sort_existing(apply=apply, limit=limit, echo=click.echo)
    click.echo(
        ("Made " if apply else "Would make ")
        + str(counts["other"])
        + " Other Items. Records: "
        + str(counts["records"])
        + ", kept as records because they have a Unit: "
        + str(counts["kept"])
        + ", category unknown: "
        + str(counts["unknown"])
        + (
            ", sold Other Items linked to their receipts: " + str(counts["claimed"])
            if apply
            else ""
        )
        + "."
    )
    if not apply:
        click.echo("This was a dry run. Add --apply to save the changes.")


@click.command("park-ended-listings")
@click.option("--apply", is_flag=True, help="Save the changes; without it, only show.")
@click.option(
    "--limit", type=int, help="Park at most this many listings, oldest first."
)
@with_appcontext
def park_ended_listings(apply, limit):
    """Add listings that were ended by hand to the Parked Listings queue."""
    from fbc.sales import parking

    parking.park_ended(apply=apply, limit=limit, echo=click.echo)
    if not apply:
        click.echo("This was a dry run. Add --apply to park them.")


@click.command("pictures-import")
@click.option("--apply", is_flag=True, help="Copy the pictures; without it, only show.")
@click.option("--limit", type=int, help="Read at most this many listings.")
@click.option("--item", help="Only the Units of this eBay listing (its item id).")
@click.option("--include-sold", is_flag=True, help="Also do Units that are sold.")
@with_appcontext
def pictures_import(apply, limit, item, include_sold):
    """Copy eBay listings' pictures down to the Units linked to them."""
    from fbc.ebay import picture_import

    counts = picture_import.import_pictures(
        apply=apply,
        limit=limit,
        item=item,
        include_sold=include_sold,
        echo=click.echo,
    )
    click.echo(
        ("Copied " if apply else "Would copy ")
        + str(counts["pictures"])
        + " pictures to "
        + str(counts["units"])
        + " Units from "
        + str(counts["listings"])
        + " listings. Couldn't read: "
        + str(counts["unreadable"])
        + ", no pictures on eBay: "
        + str(counts["no_pictures"])
        + ", Units already with pictures: "
        + str(counts["have"])
        + ", sold Units left out: "
        + str(counts["sold"])
        + ", mystery boxes: "
        + str(counts["mystery"])
        + "."
    )
    if not apply:
        click.echo("This was a dry run. Add --apply to copy them.")
