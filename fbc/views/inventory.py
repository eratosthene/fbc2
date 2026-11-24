from fbc.sales.archive import ArchivableViewMixin
from fbc.core.forms import CustomModelView
from flask import flash, render_template, request, url_for
from flask_appbuilder import expose
from flask_appbuilder.security.decorators import has_access
from fbc.sales import pricing
from flask_appbuilder.fieldwidgets import BS3TextFieldWidget, Select2Widget
from flask_appbuilder.models.filters import BaseFilter
from flask_appbuilder.models.sqla.interface import SQLAInterface
from sqlalchemy import exists
from wtforms import SelectField

from fbc.models.inventory import PurchaseLot, StorageBox, Unit
from fbc.models.supplies import ADDING_UNIT
from fbc.core.forms import CustomForm
from fbc.sales.widgets import SuppliesUsedField
from markupsafe import Markup
from wtforms import BooleanField, StringField
from wtforms.validators import Length, Optional


# Filters preset on the Units lists by the Rebox and "w/out Master" menu links,
# as FAB list URL args _flt_<n>_<column>: n picks the column's nth filter type
# (boolean 1 is Not Equal to, number 1 is Greater than, string 3 is Equal to).
UNSOLD_UNITS_OVER_2_FILTERS = {"_flt_1_sold": "y", "_flt_1_retail_price": "2"}
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


class FilterHasPictures(BaseFilter):
    """
    Units with (Yes) or without (No) a stored picture. Pictures have no
    relationship on Unit (see fbc/models/pictures.py), so this tests for a
    UnitPicture row. It sits on the Unit's id, and its value is "y" or "n".
    """

    name = "Has Pictures"
    arg_name = "has_pictures"

    def apply(self, query, value):
        from fbc.models.pictures import UnitPicture

        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        has = exists().where(UnitPicture.unit_id == Unit.id).correlate(Unit)
        return query.filter(~has if str(value).lower() in ("n", "no", "false") else has)


def _artist_choices():
    """Every Artist, by sort name, for the Artist dropdown (read per request)."""
    from fbc import db
    from fbc.models.discogs import Artist

    rows = db.session.query(Artist.artist_id, Artist.name, Artist.sort_name).all()
    rows.sort(key=lambda r: (r.sort_name or r.name or "").lower())
    return [(str(r.artist_id), r.sort_name or r.name) for r in rows]


class FilterDiscogsArtist(BaseFilter):
    """Units whose Discogs Release has the chosen Artist.

    A Unit with no Release never matches. A Release can have several Artists;
    any one of them picks it.
    """

    name = "Discogs Artist"
    arg_name = "discogs_artist"

    def apply(self, query, value):
        from fbc.models.discogs import Artist, DiscogsRelease

        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        if not str(value).isdigit():
            return query
        return query.filter(
            Unit.discogs_release.has(
                DiscogsRelease.artists.any(Artist.artist_id == int(value))
            )
        )


class UnitInterface(SQLAInterface):
    """Loads the Pictures counts of a page of Units in one query."""

    def query(self, *args, **kwargs):
        count, lst = super().query(*args, **kwargs)
        # Flask-AppBuilder makes the interfaces of related models from this
        # class too, so only Units get the counts.
        if self.obj is Unit:
            Unit.prime_picture_counts(lst)
        return count, lst


# The Units views share everything but the columns they list. They subclass
# this rather than UnitModelView because Flask-AppBuilder attaches related
# views by isinstance, so a subclass of UnitModelView would also show up on
# every page that lists UnitModelView as related.
class UnitModelBaseView(ArchivableViewMixin, CustomModelView):
    datamodel = UnitInterface(Unit)
    list_columns = [
        "name",
        "unit_type",
        "record_grade",
        "sleeve_grade",
        "dust_jacket_grade",
        "pressing",
        "fmt_retail_price",
        "sold",
        "purchase_lot",
        "storage_box",
        "picture_count",
        "link_column",
    ]
    label_columns = {
        "fmt_retail_price": "Retail Price",
        "link_column": "Links",
        "picture_count": "Pictures",
        "has_pictures": "Has Pictures",
        "discogs_artist": "Discogs Artist",
    }
    # A Purchase Lot sorts by its date, a Storage Box by its name.
    sort_by = {"purchase_lot": "purchase_lot.date", "storage_box": "storage_box.name"}
    search_form_extra_fields = {
        "has_pictures": SelectField(
            "Has Pictures", choices=[("y", "Yes"), ("n", "No")]
        ),
        "discogs_artist": SelectField(
            "Discogs Artist", choices=_artist_choices, widget=Select2Widget()
        ),
    }
    # Every column the page showed before it had a list of its own, plus the
    # Links, which are the same cell the list shows.
    show_columns = [
        "name",
        "link_column",
        "unit_type",
        "description",
        "discogs_release",
        "discogs_instance",
        "discogs_master",
        "ebay_listing",
        "purchase_lot",
        "storage_box",
        "sales_receipt",
        "grading",
        "record_grade",
        "record_note",
        "sleeve_grade",
        "sleeve_note",
        "dust_jacket_grade",
        "dust_jacket_note",
        "extras",
        "pressing",
        "matrix",
        "notes",
        "retail_price",
        "sold",
        "sold_price",
        "archived",
    ]
    search_columns = [
        "name",
        "unit_type",
        "description",
        "discogs_release",
        "discogs_master",
        "ebay_listing",
        "purchase_lot",
        "storage_box",
        "record_grade",
        "record_note",
        "sleeve_grade",
        "sleeve_note",
        "dust_jacket_grade",
        "dust_jacket_note",
        "extras",
        "pressing",
        "matrix",
        "notes",
        "retail_price",
        "sold",
        "archived",
        "has_pictures",
        "discogs_artist",
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
        "record_grade",
        "record_note",
        "sleeve_grade",
        "sleeve_note",
        "dust_jacket_grade",
        "dust_jacket_note",
        "extras",
        "pressing",
        "matrix",
        "notes",
        "retail_price",
        "sold",
        "sales_receipt",
        "sold_price",
    ]
    description_columns = {
        "sold_price": "Only change this if you need to. It is only for a Sales"
        " Receipt that covers several Units: it says how much of the receipt"
        " this Unit's part of the sale was. Ship This and Combine fill it in."
        " It does nothing for a receipt with one Unit.",
    }
    # Archiving is by the command and the list actions, not the form. Sold
    # Price is only for correcting a sale, so it is on Edit and not on Add
    # (which Identify Record ends in).
    add_exclude_columns = ["archived", "sold_price"]
    add_form_extra_fields = UNIT_ADD_FORM_EXTRA_FIELDS
    add_template = "pricing/add_unit.html"
    edit_template = "unit/edit.html"
    edit_form_extra_fields = UNIT_FORM_EXTRA_FIELDS

    def render_template(self, template, **kwargs):
        # The Price Research block loads from pricing() below.
        kwargs.setdefault("pricing_endpoint", self.endpoint + ".pricing")
        return super().render_template(template, **kwargs)

    @expose("/pricing")
    @has_access
    def pricing(self):
        """The Price Research block on Add Unit, loaded when it's asked for."""
        name = request.args.get("name", "")
        found = pricing.research(
            request.args.get("release") or None,
            request.args.get("master") or None,
            name,
        )
        return render_template("pricing/result.html", r=found, name=name)

    def _init_properties(self):
        super()._init_properties()
        # Not a column, so Flask-AppBuilder made no filters for it.
        for table in (self._filters._search_filters, self._filters._all_filters):
            table["has_pictures"] = [FilterHasPictures("id", self.datamodel)]
            table["discogs_artist"] = [FilterDiscogsArtist("id", self.datamodel)]
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
    # What the Releases list sorts these by: the first one alphabetically.
    sort_by = dict(
        UnitModelBaseView.sort_by,
        discogs_genre_column="release_first_genre",
        discogs_style_column="release_first_style",
    )


class PurchaseLotModelView(CustomModelView):
    datamodel = SQLAInterface(PurchaseLot)
    related_views = [UnitModelView]
    list_columns = [
        "name",
        "date",
        "purchase_price",
        "folder",
        "notes",
    ]
    show_columns = [
        "name",
        "date",
        "purchase_price",
        "folder",
        "notes",
    ]
    label_columns = {"folder": "Discogs Folder"}
    sort_by = {"folder": "folder.name"}
    show_template = "crud/purchase_lot_show.html"


class StorageBoxModelView(CustomModelView):
    datamodel = SQLAInterface(StorageBox)
    related_views = [UnitModelView]
    list_columns = ["name"]
