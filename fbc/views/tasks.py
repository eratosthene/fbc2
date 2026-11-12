"""
The Tasks menu: lists of what needs doing next (see fbc/tasks.py for what each
means). Each is an existing list with a filter on it, the way "Releases w/out
Units" is, so a task leaves the list when it's done. A fourth, eBay Drafts that
aren't published, is a filter link on the eBay Drafts list instead
(DRAFTS_TO_LIST_FILTERS in fbc/views/listing.py).
"""

from flask_appbuilder.models.filters import BaseFilter
from flask_appbuilder.models.sqla.filters import get_field_setup_query

from fbc import tasks
from fbc.views.discogs import DiscogsReleaseModelBaseView
from fbc.views.inventory import UnitModelBaseView
from fbc.views.sales import EbayOrderModelView


class FilterInSelect(BaseFilter):
    name = "Filter view where field is in a SELECT returned by a function"
    arg_name = "in_sel"

    def apply(self, query, func):
        query, field = get_field_setup_query(query, self.model, self.column_name)
        return query.filter(field.in_(func()))


class OrdersToShipView(EbayOrderModelView):
    """eBay Orders with no Sales Receipt yet: use Ship This."""

    base_filters = [["order_id", FilterInSelect, tasks.ids_orders_to_ship]]


class ReleasesNeedingUnitsView(DiscogsReleaseModelBaseView):
    """Releases with more copies in the Discogs collection than unsold Units."""

    base_filters = [["release_id", FilterInSelect, tasks.ids_releases_needing_units]]


class UnitsNeedingDraftsView(UnitModelBaseView):
    """Unsold Units over $2 with no eBay Listing and no eBay Draft."""

    base_filters = [["id", FilterInSelect, tasks.ids_units_needing_drafts]]
