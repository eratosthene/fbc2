from fbc.archive import ArchivableViewMixin
from fbc.util import CustomModelView
from flask import flash, url_for
from flask_appbuilder.fieldwidgets import BS3TextFieldWidget
from flask_appbuilder.models.sqla.interface import SQLAInterface

from fbc.models.inventory import PurchaseLot, StorageBox, Unit
from fbc.models.supplies import ADDING_UNIT
from fbc.util import CustomForm
from fbc.widgets import SuppliesUsedField
from markupsafe import Markup
from wtforms import BooleanField, StringField
from wtforms.validators import Length, Optional


# Filters preset on the Units lists by the "w/out" and Rebox menu links, as FAB
# list URL args _flt_<n>_<column>: n picks the column's nth filter type
# (relation 0 is Relation, boolean 1 is Not Equal to, number 1 is Greater than,
# string 3 is Equal to).
UNSOLD_UNITS_OVER_2_FILTERS = {"_flt_1_sold": "y", "_flt_1_retail_price": "2"}
UNITS_WITHOUT_LISTING_FILTERS = {
    "_flt_0_ebay_listing": "__None",
    **UNSOLD_UNITS_OVER_2_FILTERS,
}
UNITS_WITHOUT_MASTER_FILTERS = {"_flt_3_discogs_master": ""}

# discogs_master and sold are NOT NULL, which Flask-AppBuilder would turn into
# required fields; left blank they're stored as '' and False instead.
UNIT_FORM_EXTRA_FIELDS = {
    "discogs_master": StringField(
        "Discogs Master",
        validators=[Optional(), Length(max=21)],
        filters=[lambda value: value or ""],
        widget=BS3TextFieldWidget(),
    ),
    "sold": BooleanField("Sold", default=False),
}


# Adding a Unit can take supplies out of stock, like an inner sleeve to
# replace a missing one; editing it again can't, so it isn't counted twice.
UNIT_ADD_FORM_EXTRA_FIELDS = dict(
    UNIT_FORM_EXTRA_FIELDS,
    supplies_used=SuppliesUsedField(
        "Supplies Used",
        used_for=ADDING_UNIT,
        description="Anything this Unit needed from the Supply List, such as"
        " an inner sleeve to replace a missing one. Saving the Unit takes them"
        " out of stock.",
    ),
)


# The Units views share everything but the columns they list. They subclass
# this rather than UnitModelView because Flask-AppBuilder attaches related
# views by isinstance, so a subclass of UnitModelView would also show up on
# every page that lists UnitModelView as related.
class UnitModelBaseView(ArchivableViewMixin, CustomModelView):
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
        "discogs_master",
        "ebay_listing",
        "purchase_lot",
        "storage_box",
        "grading",
        "pressing",
        "matrix",
        "notes",
        "retail_price",
        "sold",
        "archived",
    ]
    edit_columns = [
        "name",
        "unit_type",
        "description",
        "discogs_release",
        "discogs_master",
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
    # Archiving is by the command and the list actions, not the form.
    add_exclude_columns = ["archived"]
    add_form_extra_fields = UNIT_ADD_FORM_EXTRA_FIELDS
    edit_form_extra_fields = UNIT_FORM_EXTRA_FIELDS

    def _init_properties(self):
        super()._init_properties()
        self.add_columns = self.add_columns + ["supplies_used"]

    def _init_forms(self):
        super(CustomModelView, self)._init_forms()
        self.add_form = type("CustomForm", (CustomForm, self.add_form), {})

    def pre_add(self, item):
        # The form left {supply: count} on the Unit; the supplies are saved
        # along with it.
        for supply, count in getattr(item, "supplies_used", {}).items():
            supply.use(count, ADDING_UNIT, unit=item)

    def post_add(self, item):
        # A Unit without a price is easy to miss and drops out of the lists
        # filtered on price, but a few really have none, so it still saves.
        if item.retail_price is None:
            flash(
                Markup(
                    '{} has no Retail Price. <a href="{}">Edit it</a> to add one.'
                ).format(item.name, url_for(self.endpoint + ".edit", pk=item.id)),
                "warning",
            )


class UnitModelView(UnitModelBaseView):
    pass


class UnitModelReboxView(UnitModelBaseView):
    """Units with the columns that help decide which box each belongs in."""

    list_columns = [
        "name",
        "unit_type",
        "storage_box",
        "discogs_genre_column",
        "discogs_style_column",
        "link_column",
    ]
    label_columns = {
        "link_column": "Links",
        "discogs_genre_column": "Genres",
        "discogs_style_column": "Styles",
    }


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

    def _get_list_widget(self, *args, **kwargs):
        # Work every lot's figures out once, rather than a set of queries
        # per row. They live in g, so for this request only, and the rows
        # are read from them as the page renders, after this returns.
        from flask import g
        from fbc import finance

        g.lot_figures = finance.lot_figures()
        return super()._get_list_widget(*args, **kwargs)


class StorageBoxModelView(CustomModelView):
    datamodel = SQLAInterface(StorageBox)
    related_views = [UnitModelView]
    list_columns = ["name"]
