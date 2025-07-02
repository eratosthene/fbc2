from flask_appbuilder import ModelView
from flask_appbuilder.models.sqla.interface import SQLAInterface
from flask_appbuilder.models.sqla.filters import FilterEqualFunction

from fbc.models.inventory import PurchaseLot, StorageBox, Unit
from fbc.util import CustomForm


class UnitModelView(ModelView):
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
        super(ModelView, self)._init_forms()
        self.add_form = type("CustomForm", (CustomForm, self.add_form), {})


class UnitModelNoListingView(ModelView):
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


class UnitModelNoDiscogsView(ModelView):
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


class PurchaseLotModelView(ModelView):
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


class StorageBoxModelView(ModelView):
    datamodel = SQLAInterface(StorageBox)
    related_views = [UnitModelView]
    list_columns = ["name"]
