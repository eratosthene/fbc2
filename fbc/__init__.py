# ruff: noqa: E402
import importlib
import logging
import os
import sys
from urllib.parse import urlencode

import click
import sqlalchemy as sa
from flask import render_template

from flask import Flask
from flask import send_from_directory
from flask_appbuilder import AppBuilder, Model
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate, stamp, upgrade
from fbc import devmode, settings
from fbc.index import MyIndexView

logging.basicConfig(format="%(asctime)s:%(levelname)s:%(name)s:%(message)s")
logging.getLogger().setLevel(logging.DEBUG)

app = Flask(__name__)
# config.py, with any FBC_CONFIG_* overrides from the environment.
settings.load(app.config)
# Before anything reads the eBay and Discogs settings (see fbc/devmode.py),
# and after the overrides, so it sees the final values.
devmode.init_app(app)
# Flask-AppBuilder's page layout, with the Dev Mode banner added.
app.config.setdefault("FAB_BASE_TEMPLATE", "layout.html")
# Flask-AppBuilder's Model is the models' base class, so passing it here puts
# its metadata (ours and the ab_* tables) on db.
db = SQLAlchemy(app, model_class=Model)
migrate = Migrate(app, db)
# FBC_SKIP_VIEWS=1 is for scripts and commands that only need `app` and `db`.
# Flask-AppBuilder's security setup and the views' registration run a few
# hundred database queries on every import, which is most of the startup time.
# Anything that serves pages or needs `appbuilder` must not set it. See
# "Writing scripts" in docs/development.md.
#
# The `flask` commands below that don't need the views turn it on themselves,
# as the app is imported before Flask knows which command it will run. A value
# set in the environment, such as FBC_SKIP_VIEWS=0, wins.
SKIP_VIEWS_COMMANDS = {
    "combine-receipts",
    "backfill-earnings",
    "set-listing-skus",
    "archive",
    "sync",
    "scheduler",
}
_skip_views = os.environ.get("FBC_SKIP_VIEWS")
if _skip_views is None:
    _skip_views = "1" if SKIP_VIEWS_COMMANDS & set(sys.argv[1:5]) else ""
SKIP_VIEWS = _skip_views.lower() in ("1", "true", "yes")

if SKIP_VIEWS:
    # The views would have imported the models, and a relationship names its
    # target by string, so a query fails unless every model is loaded.
    # (import_module, so the names don't replace fbc.discogs and the like.)
    for _model_module in (
        "discogs",
        "ebay",
        "expenses",
        "inventory",
        "sales",
        "supplies",
        "sync",
    ):
        importlib.import_module("fbc.models." + _model_module)
else:
    # Flask-AppBuilder reads the config and sets up its security tables through
    # current_app, so it needs an app context, here and when adding views below.
    with app.app_context():
        appbuilder = AppBuilder(app, db.session, indexview=MyIndexView)

    @app.errorhandler(404)
    def page_not_found(e):
        return (
            render_template(
                "404.html",
                base_template=appbuilder.base_template,
                appbuilder=appbuilder,
            ),
            404,
        )


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "favicon.ico",
        mimetype="image/vnd.microsoft.icon",
    )


@app.cli.command("setup-db")
def setup_db():
    """Create a new database from the models, or migrate an existing one."""
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


@app.cli.command("combine-receipts")
@click.option("--apply", is_flag=True, help="Save the changes; without it, only show.")
@click.option(
    "--receipts",
    help="Combine just these Sales Receipts, by id (e.g. 516,517), into one.",
)
def combine_receipts(apply, receipts):
    """Combine Sales Receipts that stand for one shipment."""
    from fbc.combine import combine_all

    only = [int(i) for i in receipts.split(",")] if receipts else None
    count = combine_all(apply=apply, only=only, echo=click.echo)
    click.echo(("Combined " if apply else "Would combine ") + str(count) + " groups.")


@app.cli.command("backfill-earnings")
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
def backfill_earnings(apply, update_net, since, limit):
    """Give past Sales Receipts their earnings lines from eBay."""
    from fbc.shipping import backfill_earnings

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


@app.cli.command("archive")
@click.option("--apply", is_flag=True, help="Save the changes; without it, only show.")
def archive(apply):
    """Archive old, finished Units, listings, orders and receipts."""
    from fbc.archive import archive_all

    archive_all(apply=apply, echo=click.echo)
    if not apply:
        click.echo("This was a dry run. Add --apply to archive them.")


@app.cli.group("sync")
def sync_group():
    """Run a Discogs or eBay sync, as its Sync button does."""


def _run_sync(job):
    """
    Run a fbc.syncjobs Job in the foreground, printing its log. Exits 1 if it
    failed, logged an error (the syncs carry on from some) or is already
    running, from a button or another command.
    """
    from fbc import sync

    # logging.basicConfig above prints every record on stderr already; the
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
        result = sync.run(job.kind, job.title, job.fn, echo=click.echo)
    except sync.Busy as e:
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
    from fbc import syncjobs

    _run_sync(syncjobs.discogs_job())


@sync_group.command("ebay-listings")
def sync_ebay_listings_command():
    """Sync eBay's active listings."""
    from fbc import syncjobs

    _run_sync(syncjobs.ebay_listings_job())


@sync_group.command("ebay-orders")
@click.option("--update", is_flag=True, help="Also refresh the orders fbc already has.")
@click.option("--deep", is_flag=True, help="Look back 88 days, not 10.")
def sync_ebay_orders_command(update, deep):
    """Sync recent eBay orders."""
    from fbc import syncjobs

    _run_sync(syncjobs.ebay_orders_job(update=update, deep=deep))


@app.cli.command("scheduler")
def scheduler_command():
    """Run the syncs on their intervals, until stopped (see fbc/scheduler.py)."""
    from fbc import scheduler

    scheduler.loop(app.config)


@app.cli.command("set-listing-skus")
@click.option(
    "--apply", is_flag=True, help="Change the listings; without it, only show."
)
@click.option(
    "--overwrite",
    is_flag=True,
    help="Also replace a SKU typed on eBay that isn't fbc-<unit id>.",
)
@click.option("--item", help="Only this eBay item ID.")
@click.option("--limit", type=int, help="Change at most this many listings.")
@click.option(
    "--csv",
    "csv_path",
    default="skipped-listing-skus.csv",
    show_default=True,
    help="Where to write the listings it skipped, and why. - prints the CSV"
    " to stdout, with the rest of the output on stderr.",
)
def set_listing_skus(apply, overwrite, item, limit, csv_path):
    """Set fbc-<unit id> as the SKU on live listings that lack it."""
    from fbc import skus

    # With the CSV on stdout, say everything else on stderr so it stays clean.
    to_stdout = csv_path == "-"

    def say(text):
        click.echo(text, err=to_stdout)

    try:
        result = skus.set_skus(
            apply=apply, overwrite=overwrite, item=item, limit=limit, echo=say
        )
    except skus.EbayError as e:
        raise click.ClickException(str(e))
    counts = result["counts"]
    say("")
    say(("Set " if apply else "Would set ") + str(counts[skus.SET]) + ".")
    say("Already right: " + str(counts[skus.RIGHT]) + ".")
    for reason in skus.SKIP_REASONS:
        say("Skipped, " + reason + ": " + str(counts[reason]) + ".")
    text = skus.skipped_csv(result["skipped"])
    if to_stdout:
        click.echo(text, nl=False)
    else:
        with open(csv_path, "w", newline="") as f:
            f.write(text)
        say("Wrote " + str(len(result["skipped"])) + " skipped to " + csv_path + ".")
    if not apply:
        say("This was a dry run. Add --apply to change the listings.")


def _register_views():
    """Import and add every view and menu entry (about 460 database queries)."""
    # The views below import the models, which need `db` and `appbuilder` to exist
    # first, so these imports can't move to the top of the file.
    from fbc.views.discogs import (
        DiscogsReleaseModelView,
        DiscogsReleaseModelNoUnitView,
        ArtistModelView,
        GenreModelView,
        StyleModelView,
        FolderModelView,
    )
    from fbc.views.sales import (
        EbayOrderModelView,
        EbayListingModelView,
        MysteryBoxesView,
        SalesReceiptModelView,
    )
    from fbc.views.listing import EbayDraftModelView, DRAFTS_TO_LIST_FILTERS
    from fbc.views.tasks import (
        OrdersToShipView,
        ReleasesNeedingUnitsView,
        UnitsNeedingDraftsView,
    )
    from fbc.models.sync import SyncRecord  # noqa: F401  (so its table exists)
    from fbc.views.connect import ConnectDiscogsView, ConnectEbayView
    from fbc.views.supplies import (
        ReconcileSuppliesView,
        SupplyModelView,
        SupplyUseModelView,
        PurchaseOrderModelView,
        PurchaseOrderSupplyModelView,
    )
    from fbc.views.expenses import ExpenseModelView
    from fbc.views.identify import IdentifyRecordView
    from fbc.views.inventory import (
        UnitModelView,
        PurchaseLotModelView,
        StorageBoxModelView,
        UnitModelReboxView,
        UNSOLD_UNITS_OVER_2_FILTERS,
        UNITS_WITHOUT_LISTING_FILTERS,
        UNITS_WITHOUT_MASTER_FILTERS,
    )

    with app.app_context():
        units = appbuilder.add_view(UnitModelView, "Units", category="Inventory")
        units_list = units.route_base + "/list/?"
        appbuilder.add_link(
            "Units w/out Listings",
            href=units_list + urlencode(UNITS_WITHOUT_LISTING_FILTERS),
            category="Inventory",
        )
        rebox = appbuilder.add_view_no_menu(UnitModelReboxView)
        appbuilder.add_link(
            "Rebox Units",
            href=rebox.route_base + "/list/?" + urlencode(UNSOLD_UNITS_OVER_2_FILTERS),
            category="Inventory",
        )
        appbuilder.add_link(
            "Units w/out Master",
            href=units_list + urlencode(UNITS_WITHOUT_MASTER_FILTERS),
            category="Inventory",
        )
        appbuilder.add_view(PurchaseLotModelView, "Purchase Lots", category="Inventory")
        appbuilder.add_view(IdentifyRecordView, "Identify Record", category="Inventory")
        appbuilder.add_view(StorageBoxModelView, "Storage Boxes", category="Inventory")
        appbuilder.add_view(DiscogsReleaseModelView, "Releases", category="Discogs")
        appbuilder.add_view(
            DiscogsReleaseModelNoUnitView, "Releases w/out Units", category="Discogs"
        )
        appbuilder.add_view(ArtistModelView, "Artists", category="Discogs")
        appbuilder.add_view(GenreModelView, "Genres", category="Discogs")
        appbuilder.add_view(StyleModelView, "Styles", category="Discogs")
        appbuilder.add_view(FolderModelView, "Folders", category="Discogs")
        appbuilder.add_view(ConnectDiscogsView, "Connect Discogs", category="Discogs")
        appbuilder.add_view(SalesReceiptModelView, "Sales Receipts", category="Sales")
        appbuilder.add_view(EbayListingModelView, "eBay Listings", category="Sales")
        appbuilder.add_view(EbayOrderModelView, "eBay Orders", category="Sales")
        drafts = appbuilder.add_view(
            EbayDraftModelView, "eBay Drafts", category="Sales"
        )
        appbuilder.add_view(MysteryBoxesView, "Mystery Boxes", category="Sales")
        appbuilder.add_view(ConnectEbayView, "Connect eBay", category="Sales")
        appbuilder.add_view(OrdersToShipView, "Orders to Ship", category="Tasks")
        appbuilder.add_view(
            ReleasesNeedingUnitsView, "Releases to Make Units", category="Tasks"
        )
        appbuilder.add_view(UnitsNeedingDraftsView, "Units to Draft", category="Tasks")
        appbuilder.add_link(
            "Drafts to List",
            href=drafts.route_base + "/list/?" + urlencode(DRAFTS_TO_LIST_FILTERS),
            category="Tasks",
        )
        appbuilder.add_view(SupplyModelView, "Supply List", category="Supplies")
        appbuilder.add_view(
            ReconcileSuppliesView, "Reconcile Supplies", category="Supplies"
        )
        appbuilder.add_view(
            PurchaseOrderModelView, "Purchase Orders", category="Supplies"
        )
        appbuilder.add_view_no_menu(PurchaseOrderSupplyModelView)
        appbuilder.add_view(SupplyUseModelView, "Supply Use", category="Supplies")
        appbuilder.add_view(ExpenseModelView, "Expenses", category="Supplies")


if not SKIP_VIEWS:
    _register_views()
