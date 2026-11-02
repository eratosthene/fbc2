# ruff: noqa: E402
import logging
import os
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
# Flask-AppBuilder reads the config and sets up its security tables through
# current_app, so it needs an app context, here and when adding views below.
with app.app_context():
    appbuilder = AppBuilder(app, db.session, indexview=MyIndexView)


@app.errorhandler(404)
def page_not_found(e):
    return (
        render_template(
            "404.html", base_template=appbuilder.base_template, appbuilder=appbuilder
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
    SalesReceiptModelView,
)
from fbc.views.listing import EbayDraftModelView
from fbc.views.connect import ConnectDiscogsView, ConnectEbayView
from fbc.views.supplies import (
    ReconcileSuppliesView,
    SupplyModelView,
    SupplyUseModelView,
    PurchaseOrderModelView,
)
from fbc.views.expenses import ExpenseModelView
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
    appbuilder.add_view(EbayDraftModelView, "eBay Drafts", category="Sales")
    appbuilder.add_view(ConnectEbayView, "Connect eBay", category="Sales")
    appbuilder.add_view(SupplyModelView, "Supply List", category="Supplies")
    appbuilder.add_view(
        ReconcileSuppliesView, "Reconcile Supplies", category="Supplies"
    )
    appbuilder.add_view(PurchaseOrderModelView, "Purchase Orders", category="Supplies")
    appbuilder.add_view(SupplyUseModelView, "Supply Use", category="Supplies")
    appbuilder.add_view(ExpenseModelView, "Expenses", category="Supplies")
