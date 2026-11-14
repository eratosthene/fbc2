import logging

from flask import abort, flash, redirect, request, url_for
from flask_appbuilder import expose, has_access

from fbc import devmode, discogs_listing, localtime
from fbc.syncnote import DISCOGS, DISCOGS_LISTINGS, SyncedViewMixin
from fbc.util import CustomModelView
from flask_appbuilder.models.sqla.interface import SQLAInterface
from flask_appbuilder.models.sqla.filters import (
    get_field_setup_query,
)
from flask_appbuilder.models.filters import BaseFilter

from fbc.models.discogs import (
    Artist,
    DiscogsListing,
    DiscogsRelease,
    Folder,
    Genre,
    Style,
)
from fbc.widgets import (
    DiscogsListingListWidget,
    DiscogsReleaseListWidget,
)
from fbc.views.inventory import UnitModelView


logger = logging.getLogger()


class FilterNotInFunction(BaseFilter):
    name = "Filter view where field is not in a list returned by a function"
    arg_name = "not_inf"

    def apply(self, query, func):
        query, field = get_field_setup_query(query, self.model, self.column_name)
        return query.filter(~field.in_(func()))


def get_unit_releases():
    from fbc.models.inventory import Unit
    from fbc import db

    releases = [
        r.discogs_release_id
        for r in db.session.query(Unit)
        .filter(Unit.discogs_release_id.isnot(None))
        .distinct()
    ]
    return releases


# See UnitModelBaseView for why the Releases views share a base class rather
# than subclassing DiscogsReleaseModelView.
class DiscogsReleaseModelBaseView(SyncedViewMixin, CustomModelView):
    synced_by = [DISCOGS]
    datamodel = SQLAInterface(DiscogsRelease)
    base_order = ("date_added", "desc")
    list_widget = DiscogsReleaseListWidget
    related_views = [UnitModelView]
    list_columns = [
        "date_added",
        "title",
        "artists",
        "year",
        "master_year",
        "genres",
        "styles",
        "purchase_lot",
        "link_column",
    ]
    label_columns = {"link_column": "Links"}
    add_columns = [
        "release_id",
        "title",
        "artists",
        "year",
        "master_id",
        "master_year",
        "genres",
        "styles",
        "discogs_instances",
        "released",
        "folder",
    ]
    edit_columns = [
        "title",
        "artists",
        "year",
        "master_id",
        "master_year",
        "genres",
        "styles",
        "discogs_instances",
        "released",
        "folder",
    ]


class DiscogsReleaseModelView(DiscogsReleaseModelBaseView):
    pass


class DiscogsReleaseModelNoUnitView(DiscogsReleaseModelBaseView):
    """Releases that no Unit has been created from yet."""

    base_filters = [["release_id", FilterNotInFunction, get_unit_releases]]


class ArtistModelView(SyncedViewMixin, CustomModelView):
    synced_by = [DISCOGS]
    datamodel = SQLAInterface(Artist)
    related_views = [DiscogsReleaseModelView]
    list_columns = [
        "name",
        "sort_name",
        "artist_id",
    ]
    add_columns = ["artist_id", "name", "sort_name"]
    related_views = [DiscogsReleaseModelView]


class GenreModelView(SyncedViewMixin, CustomModelView):
    synced_by = [DISCOGS]
    datamodel = SQLAInterface(Genre)
    base_order = ("name", "desc")
    related_views = [DiscogsReleaseModelView]


class StyleModelView(SyncedViewMixin, CustomModelView):
    synced_by = [DISCOGS]
    datamodel = SQLAInterface(Style)
    base_order = ("name", "desc")
    related_views = [DiscogsReleaseModelView]


class FolderModelView(SyncedViewMixin, CustomModelView):
    synced_by = [DISCOGS]
    datamodel = SQLAInterface(Folder)
    list_columns = ["name", "folder_id"]
    base_order = ("name", "desc")
    add_columns = ["folder_id", "name"]
    related_views = [DiscogsReleaseModelView]


class DiscogsListingModelView(SyncedViewMixin, CustomModelView):
    """
    The Discogs Marketplace inventory as the last sync read it. The rows
    can't be edited: they're changed on Discogs and the sync picks the change
    up. The only writes to Discogs are the two buttons List on Discogs
    (from_unit, which posts a Draft) and Make it For Sale (make_for_sale),
    each its own explicit action.
    """

    datamodel = SQLAInterface(DiscogsListing)
    show_template = "discogs_listing/show.html"
    synced_by = [DISCOGS_LISTINGS]
    base_permissions = ["can_list", "can_show", "can_from_unit", "can_make_for_sale"]
    base_order = ("posted", "desc")
    formatters_columns = localtime.formatters("posted", "last_seen")
    list_widget = DiscogsListingListWidget
    list_columns = [
        "listing_id",
        "title",
        "unit",
        "status",
        "ended",
        "posted",
        "fmt_price",
        "condition",
        "sleeve_condition",
        "location",
        "fmt_url",
    ]
    search_columns = [
        "listing_id",
        "release_id",
        "title",
        "unit",
        "status",
        "ended",
        "price",
        "condition",
        "sleeve_condition",
        "external_id",
        "location",
    ]
    show_columns = [
        "listing_id",
        "release_id",
        "title",
        "unit",
        "status",
        "ended",
        "last_seen",
        "posted",
        "fmt_price",
        "condition",
        "sleeve_condition",
        "external_id",
        "location",
        "fmt_url",
    ]
    label_columns = {
        "listing_id": "Listing ID",
        "release_id": "Release ID",
        "fmt_price": "Price",
        "fmt_url": "URL",
        "external_id": "External ID (Private Comments)",
    }
    description_columns = {
        "ended": "True once the listing is sold, or no longer in the Discogs inventory.",
        "unit": "Found from the External ID, which fbc sets to fbc-<Unit ID>.",
    }

    @expose("/show/<pk>", methods=["GET"])
    @has_access
    def show(self, pk):
        row = self.datamodel.get(pk)
        if not row:
            abort(404)
        widgets = self._show(pk)
        unit = row.unit
        return self.render_template(
            self.show_template,
            pk=pk,
            title=self.show_title,
            widgets=widgets,
            related_views=self._related_views,
            listing=row,
            can_make_for_sale=(
                not row.ended
                and row.status == discogs_listing.DRAFT
                and not (unit and unit.sold)
            ),
            ebay_notes=discogs_listing.ebay_conflicts(unit) if unit else [],
        )

    @expose("/fromunit/<int:pk>", methods=["GET", "POST"])
    @has_access
    def from_unit(self, pk):
        """
        List a Unit on Discogs. The GET shows a form, filled in from the
        Unit; only the POST, from its button, writes to Discogs, and it
        creates a Draft that buyers can't see.
        """
        from fbc import db
        from fbc.models.inventory import Unit

        unit = db.session.get(Unit, pk)
        if not unit:
            abort(404)
        problem = discogs_listing.problem_with(unit)
        existing = discogs_listing.open_listing(unit)
        if existing:
            flash(problem, "warning")
            return redirect(
                url_for("DiscogsListingModelView.show", pk=existing.listing_id)
            )
        values = discogs_listing.defaults(unit)
        errors = []
        if problem:
            errors.append(problem)
        elif devmode.on():
            errors.append(devmode.message("listing on Discogs"))
        elif request.method == "POST":
            # What the owner typed replaces the starting values.
            values = {
                k: request.form.get(k, "")
                for k in (
                    "release_id",
                    "condition",
                    "sleeve_condition",
                    "price",
                    "comments",
                    "weight",
                    "location",
                )
            }
            values["allow_offers"] = bool(request.form.get("allow_offers"))
            values["external_id"] = discogs_listing.defaults(unit)["external_id"]
            payload, errors = discogs_listing.clean_form(request.form)
            if not errors:
                try:
                    row, adopted = discogs_listing.create_listing(unit, payload)
                except discogs_listing.PostUncertain as e:
                    errors.append(
                        str(e) + " The listing may or may not have been made. Press"
                        " the button again: fbc checks Discogs for it first, so"
                        " it won't be made twice."
                    )
                except discogs_listing.ListingError as e:
                    errors.append(str(e))
                else:
                    flash(
                        "Found this Unit's listing already on Discogs and saved it."
                        if adopted
                        else "Posted to Discogs as a Draft. Buyers can't see it yet.",
                        "success",
                    )
                    return redirect(
                        url_for("DiscogsListingModelView.show", pk=row.listing_id)
                    )
        return self.render_template(
            "discogs_listing/form.html",
            title="List on Discogs",
            unit=unit,
            values=values,
            errors=errors,
            blocked=bool(problem) or devmode.on(),
            media_conditions=discogs_listing.MEDIA_CONDITIONS,
            sleeve_conditions=discogs_listing.SLEEVE_CONDITIONS,
            ebay_notes=discogs_listing.ebay_conflicts(unit),
        )

    @expose("/makeforsale/<pk>", methods=["POST"])
    @has_access
    def make_for_sale(self, pk):
        """Change a Draft to For Sale on Discogs. This makes it live."""
        row = self.datamodel.get(pk)
        if not row:
            abort(404)
        back = redirect(url_for("DiscogsListingModelView.show", pk=row.listing_id))
        if devmode.on():
            flash(devmode.message("selling on Discogs"), "warning")
            return back
        if row.unit and row.unit.sold:
            flash("This Unit is sold.", "danger")
            return back
        try:
            discogs_listing.make_for_sale(row)
        except discogs_listing.ListingError as e:
            flash(str(e), "danger")
        else:
            flash("This listing is now For Sale on Discogs.", "success")
        return back
