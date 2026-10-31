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
from fbc.index import MyIndexView

logging.basicConfig(format="%(asctime)s:%(levelname)s:%(name)s:%(message)s")
logging.getLogger().setLevel(logging.DEBUG)

app = Flask(__name__)
app.config.from_object("config")
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
    SupplyModelView,
    PurchaseOrderModelView,
)
from fbc.views.inventory import (
    UnitModelView,
    PurchaseLotModelView,
    StorageBoxModelView,
    UnitModelReboxView,
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
    appbuilder.add_view(UnitModelReboxView, "Rebox Units", category="Inventory")
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
    appbuilder.add_view(PurchaseOrderModelView, "Purchase Orders", category="Supplies")
