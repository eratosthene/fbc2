import logging
from fbc import localtime
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
    The Discogs Marketplace inventory as the last sync read it. Read only:
    listings are changed on Discogs and the sync picks the change up.
    """

    datamodel = SQLAInterface(DiscogsListing)
    synced_by = [DISCOGS_LISTINGS]
    base_permissions = ["can_list", "can_show"]
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
