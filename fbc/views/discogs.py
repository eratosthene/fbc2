import logging

from flask import abort, flash, redirect, request, url_for
from flask_appbuilder import expose, has_access

from markupsafe import Markup

from fbc.discogs import listing as discogs_listing
from fbc.discogs.removal import discogs_plan, remove_from_discogs
from fbc.sales.ship import (
    discogs_earnings,
    discogs_shipment_units,
    other_marketplace_notes,
    ship_discogs_order,
)
from fbc.sales.stock import fmt_supplies, stock_problems, supplies_plan, supply_counts
from fbc.core import devmode, localtime
from fbc.sync.notes import (
    DISCOGS,
    DISCOGS_LISTINGS,
    DISCOGS_ORDERS,
    SyncedViewMixin,
)
from fbc.core.forms import CustomModelView
from fbc.pictures import store
from fbc.sales.archive import ArchivableViewMixin
from flask_appbuilder.models.sqla.interface import SQLAInterface

from fbc.models.discogs import (
    Artist,
    DiscogsListing,
    DiscogsOrder,
    DiscogsRelease,
    Folder,
    Genre,
    Style,
)
from fbc.core.widgets import (
    DiscogsListingListWidget,
    DiscogsOrderListWidget,
    DiscogsReleaseListWidget,
)
from fbc.views.inventory import UnitModelView


logger = logging.getLogger()


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
    # Each of these lists several things, so it sorts by the first one
    # alphabetically. Purchase Lot is a method, sorted through @renders.
    sort_by = {
        "artists": "first_artist",
        "genres": "first_genre",
        "styles": "first_style",
    }
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


# Filters preset on the Discogs Listings list by the Tasks menu link: status
# Equal to Draft (string filter 3) and ended Not Equal to yes (boolean filter 1;
# an Equal filter can't be given "no"), as fbc.sales.tasks.ids_discogs_drafts_to_list.
DISCOGS_DRAFTS_TO_LIST_FILTERS = {"_flt_3_status": "Draft", "_flt_1_ended": "y"}


class DiscogsListingModelView(SyncedViewMixin, CustomModelView):
    """
    The Discogs Marketplace inventory as the last sync read it. The rows
    can't be edited: they're changed on Discogs and the sync picks the change
    up. The only writes to Discogs are the two buttons List on Discogs
    (from_unit, which posts a Draft) and Make it For Sale (make_for_sale),
    each its own explicit action. An ended listing can also be deleted from
    fbc (its row only, nothing on Discogs), to clear out test data.
    """

    datamodel = SQLAInterface(DiscogsListing)
    show_template = "discogs_listing/show.html"
    synced_by = [DISCOGS_LISTINGS]
    base_permissions = [
        "can_list",
        "can_show",
        "can_delete",
        "can_from_unit",
        "can_make_for_sale",
    ]
    base_order = ("posted", "desc")
    sort_by = {"unit": "unit.name"}
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

    def pre_delete(self, item):
        """
        Only an ended listing goes: a live one would come back at the next
        sync, with nothing on Discogs changed. Deleting is only fbc's row.
        """
        if not item.ended:
            raise ValueError(
                "Only an ended listing can be deleted here. Delete it on Discogs"
                " first, then run Sync Discogs Listings."
            )

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
            unit=unit,
            pictures=store.for_unit(unit) if unit else [],
            picture_name=store.display_name,
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
        existing = DiscogsListing.open_for(unit)
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
            return back
        flash("This listing is now For Sale on Discogs.", "success")
        # For Sale is the last step: the Drafts to List queue is next.
        return self.back_to_list()


class DiscogsOrderModelView(SyncedViewMixin, ArchivableViewMixin, CustomModelView):
    """
    Discogs Marketplace orders as the last sync read them. An order is worked
    on (invoiced, marked shipped) on Discogs, and the sync picks the change
    up; Ship This only records the sale in fbc. Rows can be deleted from fbc (only fbc's row), to clear out
    test data; the next sync brings back any that Discogs still has.
    """

    # Items link to Units through listings, which the listings sync keeps.
    synced_by = [DISCOGS_ORDERS, DISCOGS_LISTINGS]
    view_note = (
        "Discogs's API gives an order's total and its selling fee but not its"
        " payment processing fee or the label cost, so a Discogs sale's Net"
        " Sold is the total less its fee until you correct it on its Sales"
        " Receipt."
    )
    datamodel = SQLAInterface(DiscogsOrder)
    base_permissions = [
        "can_list",
        "can_show",
        "can_delete",
        "can_archive",
        "can_unarchive",
        "can_ship",
    ]
    base_order = ("created_time", "desc")
    list_widget = DiscogsOrderListWidget
    formatters_columns = localtime.formatters("created_time", "last_activity")
    list_columns = [
        "created_time",
        "fmt_url",
        "fmt_title",
        "buyer",
        "status",
        "fmt_total",
        "fmt_fee",
        "links",
    ]
    search_columns = [
        "order_id",
        "buyer",
        "status",
        "total",
        "fee",
        "created_time",
        "archived",
    ]
    show_columns = [
        "order_id",
        "created_time",
        "last_activity",
        "buyer",
        "status",
        "fmt_title",
        "fmt_shipping",
        "shipping_method",
        "fmt_fee",
        "fmt_total",
        "fmt_url",
    ]
    label_columns = {
        "fmt_url": "Order Id",
        "fmt_title": "Title",
        "fmt_total": "Total",
        "fmt_fee": "Discogs Fee",
        "fmt_shipping": "Shipping",
        "links": "Links",
    }
    description_columns = {
        "fmt_fee": "Discogs's selling fee. Its payment processing fee isn't in the API.",
        "fmt_total": "Items and shipping, as Discogs works it out.",
    }

    def _order(self, pk):
        order = self.datamodel.get(pk)
        if not order:
            abort(404)
        return order

    @expose("/ship/<pk>", methods=["GET", "POST"])
    @has_access
    def ship(self, pk):
        """
        Ship This for a Discogs order: record a Sales Receipt for it, mark its
        Units sold, take the supplies used out of stock and remove the Units'
        instances from the Discogs collection. It doesn't change the order on
        Discogs: invoice it and mark it shipped there. GET shows what will
        happen; POST does it.
        """
        order = self._order(pk)
        back = self.back_to_list()
        if order.sales_receipts:
            flash("This order already has a Sales Receipt.", "warning")
            return back
        if order.is_cancelled():
            flash("This order was cancelled, so there's nothing to ship.", "warning")
            return back
        units, problems = discogs_shipment_units(order)
        if units is None:
            for problem in problems:
                flash(problem, "warning")
            flash(
                Markup(
                    "Ship This can't tell which Units these are, so ship it by"
                    ' hand: mark the Units sold and <a href="{0}">add its Sales'
                    " Receipt</a>."
                ).format(order.add_receipt_url()),
                "warning",
            )
            return back
        values = request.form if request.method == "POST" else None
        supplies = supplies_plan(units)
        lines, earned = discogs_earnings(order)
        if values is not None:
            problems = []
            try:
                net_sold = float(values.get("net_sold", "").strip().lstrip("$"))
            except ValueError:
                problems.append("Enter Net Sold as a number of dollars.")
            try:
                used = supply_counts(values, [s for s, _ in supplies])
                problems.extend(stock_problems(used))
            except ValueError as e:
                problems.append(str(e))
            if not problems:
                receipt = ship_discogs_order(order, units, net_sold, used, lines)
                names = units[0].name if len(units) == 1 else str(len(units)) + " Units"
                flash(
                    "Recorded a Sales Receipt for $"
                    + f"{net_sold:.2f}"
                    + " and marked "
                    + names
                    + " sold.",
                    "success",
                )
                for line in receipt.lines:
                    if line.kind == "MANUAL":
                        flash(
                            "Net Sold differs from Discogs's total less its fee by "
                            + line.fmt_amount()
                            + ", so the Sales Receipt keeps that as a manual"
                            " adjustment.",
                            "info",
                        )
                if used:
                    flash("Took " + fmt_supplies(used) + " out of supplies.", "success")
                for unit in units:
                    if values.get("remove_discogs-" + str(unit.id)):
                        message, category = remove_from_discogs(unit)
                        flash(message, category)
                return back
            for problem in problems:
                flash(problem, "danger")
        return self.render_template(
            "discogs_order/ship.html",
            title="Ship This",
            order=order,
            units=units,
            earned=earned,
            lines=lines,
            notes=other_marketplace_notes(units, "Discogs"),
            discogs=[(unit,) + discogs_plan(unit) for unit in units],
            supplies=supplies,
            values=values,
        )
