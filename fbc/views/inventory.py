from fbc.util import CustomModelView
from flask_appbuilder.models.sqla.interface import SQLAInterface
from flask_appbuilder.models.sqla.filters import FilterEqualFunction
from flask_appbuilder import ModelView, CompactCRUDMixin

from fbc.models.inventory import PurchaseLot, StorageBox, Unit
from fbc.util import CustomForm


class UnitModelView(CustomModelView):
    datamodel = SQLAInterface(Unit)
    list_columns = [
        "name",
        "unit_type",
        "grading",
        "pressing",
        "fmt_retail_price",
        "sold",
        "purchase_lot",
        "storage_box",
        "link_column",
    ]
    label_columns = {"fmt_retail_price": "Retail Price", "link_column": "Links"}
    search_columns = [
        "name",
        "unit_type",
        "description",
        "discogs_release",
        "ebay_listing",
        "purchase_lot",
        "storage_box",
        "grading",
        "pressing",
        "matrix",
        "notes",
        "retail_price",
        "sold",
    ]
    edit_columns = [
        "name",
        "unit_type",
        "description",
        "discogs_release",
        "ebay_listing",
        "purchase_lot",
        "storage_box",
        "grading",
        "pressing",
        "matrix",
        "notes",
        "retail_price",
        "sold",
        "sales_receipt",
    ]

    def _init_forms(self):
        super(CustomModelView, self)._init_forms()
        self.add_form = type("CustomForm", (CustomForm, self.add_form), {})


class UnitModelNoListingView(CustomModelView):
    datamodel = SQLAInterface(Unit)
    base_filters = [
        ["ebay_listing_id", FilterEqualFunction, lambda: None],
    ]
    list_columns = [
        "name",
        "unit_type",
        "grading",
        "pressing",
        "fmt_retail_price",
        "sold",
        "purchase_lot",
        "storage_box",
        "link_column",
    ]
    label_columns = {"fmt_retail_price": "Retail Price", "link_column": "Links"}
    search_columns = [
        "name",
        "unit_type",
        "description",
        "discogs_release",
        "ebay_listing",
        "purchase_lot",
        "storage_box",
        "grading",
        "pressing",
        "matrix",
        "notes",
        "retail_price",
        "sold",
    ]


class UnitModelNoDiscogsView(CustomModelView):
    datamodel = SQLAInterface(Unit)
    base_filters = [["discogs_release_id", FilterEqualFunction, lambda: None]]
    list_columns = [
        "name",
        "unit_type",
        "grading",
        "pressing",
        "fmt_retail_price",
        "sold",
        "purchase_lot",
        "storage_box",
        "link_column",
    ]
    label_columns = {"fmt_retail_price": "Retail Price", "link_column": "Links"}
    search_columns = [
        "name",
        "unit_type",
        "description",
        "discogs_release",
        "ebay_listing",
        "purchase_lot",
        "storage_box",
        "grading",
        "pressing",
        "matrix",
        "notes",
        "retail_price",
        "sold",
    ]


class UnitModelReboxView(CompactCRUDMixin,CustomModelView):
    datamodel = SQLAInterface(Unit)
    list_columns = [
        "name",
        "unit_type",
        "storage_box",
        "discogs_genre_column",
        "link_column",
    ]
    label_columns = {"link_column": "Links", "discogs_genre_column": "Genres"}
    search_columns = [
        "name",
        "unit_type",
        "description",
        "discogs_release",
        "ebay_listing",
        "purchase_lot",
        "storage_box",
        "grading",
        "pressing",
        "matrix",
        "notes",
        "retail_price",
        "sold",
    ]
    edit_columns = [
        "storage_box"
    ]


class PurchaseLotModelView(CustomModelView):
    datamodel = SQLAInterface(PurchaseLot)
    related_views = [UnitModelView]
    list_columns = [
        "name",
        "date",
        "purchase_price",
        "notes",
        "list_total",
        "list_sold",
        "list_profit",
        "list_breakeven",
    ]
    show_columns = ["name", "date", "purchase_price", "notes", "breakdown"]
    label_columns = {
        "breakdown": "Breakdown",
        "list_total": "Units",
        "list_sold": "Sold",
        "list_profit": "Profit",
        "list_breakeven": "B/E",
    }


class StorageBoxModelView(CustomModelView):
    datamodel = SQLAInterface(StorageBox)
    related_views = [UnitModelView]
    list_columns = ["name"]
