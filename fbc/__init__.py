import logging
from flask import render_template

from flask import Flask
from flask_appbuilder import AppBuilder, SQLA

from fbc.index import MyIndexView
from fbc.views.inventory import (
    UnitModelView,
    PurchaseLotModelView,
    StorageBoxModelView,
    UnitModelNoListingView,
    UnitModelNoDiscogsView,
)
from fbc.views.discogs import (
    DiscogsReleaseModelView,
    ArtistModelView,
    GenreModelView,
    StyleModelView,
    FolderModelView,
)
from fbc.views.sales import (
    SalesReceiptModelView,
    EbayListingModelView,
    EbayOrderModelView,
)
from fbc.views.supplies import (
    SupplyModelView,
    PurchaseOrderModelView,
)

logging.basicConfig(format="%(asctime)s:%(levelname)s:%(name)s:%(message)s")
logging.getLogger().setLevel(logging.DEBUG)

app = Flask(__name__)
app.config.from_object("config")
db = SQLA(app)
appbuilder = AppBuilder(app, db.session, indexview=MyIndexView)


@appbuilder.app.errorhandler(404)
def page_not_found(e):
    return (
        render_template(
            "404.html", base_template=appbuilder.base_template, appbuilder=appbuilder
        ),
        404,
    )


db.create_all()


appbuilder.add_view(UnitModelView, "Units", category="Inventory")
appbuilder.add_view(
    UnitModelNoListingView, "Units w/out Listings", category="Inventory"
)
appbuilder.add_view(UnitModelNoDiscogsView, "Units w/out Discogs", category="Inventory")
appbuilder.add_view(PurchaseLotModelView, "Purchase Lots", category="Inventory")
appbuilder.add_view(StorageBoxModelView, "Storage Boxes", category="Inventory")
appbuilder.add_view(DiscogsReleaseModelView, "Releases", category="Discogs")
appbuilder.add_view(ArtistModelView, "Artists", category="Discogs")
appbuilder.add_view(GenreModelView, "Genres", category="Discogs")
appbuilder.add_view(StyleModelView, "Styles", category="Discogs")
appbuilder.add_view(FolderModelView, "Folders", category="Discogs")
appbuilder.add_view(SalesReceiptModelView, "Sales Receipts", category="Sales")
appbuilder.add_view(EbayListingModelView, "eBay Listings", category="Sales")
appbuilder.add_view(EbayOrderModelView, "eBay Orders", category="Sales")
appbuilder.add_view(SupplyModelView, "Supply List", category="Supplies")
appbuilder.add_view(PurchaseOrderModelView, "Purchase Orders", category="Supplies")
