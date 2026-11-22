"""
The menu: every view and menu entry, in the order the menu shows them.
fbc/__init__.py calls register_views once Flask-AppBuilder is set up.
"""

from urllib.parse import urlencode


def register_views(app, appbuilder):
    """Import and add every view and menu entry (about 460 database queries)."""
    # The views below import the models, which need `db` and `appbuilder` to exist
    # first, so these imports can't move to the top of the file.
    from fbc.views.discogs import (
        DiscogsReleaseModelView,
        ArtistModelView,
        GenreModelView,
        StyleModelView,
        FolderModelView,
        DiscogsListingModelView,
        DiscogsOrderModelView,
    )
    from fbc.views.sales import (
        EbayOrderModelView,
        EbayListingModelView,
        ListingEndModelView,
        MysteryBoxesView,
        SalesReceiptModelView,
    )
    from fbc.views.other import OtherItemModelView
    from fbc.views.listing import EbayDraftModelView, DRAFTS_TO_LIST_FILTERS
    from fbc.views.description import DescriptionTemplateView
    from fbc.views.discogs import DISCOGS_DRAFTS_TO_LIST_FILTERS
    from fbc.views.tasks import (
        DiscogsOrdersToShipView,
        FailedEndingsView,
        ParkedListingsView,
        OrdersToShipView,
        ReleasesNeedingUnitsView,
        UnitsNeedingDiscogsDraftsView,
        UnitsNeedingDraftsView,
    )
    from fbc.models.sync import SyncRecord  # noqa: F401  (so its table exists)
    from fbc.models.pictures import UnitPicture  # noqa: F401  (likewise)
    from fbc.models.templates import DescriptionTemplate  # noqa: F401  (likewise)
    from fbc.models.home import HomeLayout  # noqa: F401  (likewise)
    from fbc.views.connect import ConnectDiscogsView, ConnectEbayView
    from fbc.views.pictures import UnitPicturesView
    from fbc.views.supplies import (
        ReconcileSuppliesView,
        SupplyModelView,
        SupplyUseModelView,
        PurchaseOrderModelView,
        PurchaseOrderSupplyModelView,
    )
    from fbc.views.expenses import ExpenseModelView
    from fbc.views.reports import (
        AgingReportView,
        SuppliesReportView,
        InventoryReportView,
        LotsReportView,
        ProfitAndLossView,
        SalesReportView,
    )
    from fbc.views.identify import IdentifyRecordView
    from fbc.views.inventory import (
        UnitModelView,
        PurchaseLotModelView,
        StorageBoxModelView,
        UnitModelReboxView,
        UNSOLD_UNITS_OVER_2_FILTERS,
        UNITS_WITHOUT_MASTER_FILTERS,
    )

    with app.app_context():
        units = appbuilder.add_view(UnitModelView, "Units", category="Inventory")
        units_list = units.route_base + "/list/?"
        rebox = appbuilder.add_view_no_menu(UnitModelReboxView)
        appbuilder.add_view_no_menu(UnitPicturesView)
        appbuilder.add_link(
            "Rebox Units",
            href=rebox.route_base + "/list/?" + urlencode(UNSOLD_UNITS_OVER_2_FILTERS),
            category="Inventory",
        )
        appbuilder.add_link(
            "Units w/out Master",
            href=units_list + urlencode(UNITS_WITHOUT_MASTER_FILTERS),
            category="Inventory",
        )
        appbuilder.add_view(PurchaseLotModelView, "Purchase Lots", category="Inventory")
        appbuilder.add_view(IdentifyRecordView, "Identify Record", category="Inventory")
        appbuilder.add_view(StorageBoxModelView, "Storage Boxes", category="Inventory")
        appbuilder.add_view(DiscogsReleaseModelView, "Releases", category="Discogs")
        appbuilder.add_view(ArtistModelView, "Artists", category="Discogs")
        appbuilder.add_view(GenreModelView, "Genres", category="Discogs")
        appbuilder.add_view(StyleModelView, "Styles", category="Discogs")
        appbuilder.add_view(FolderModelView, "Folders", category="Discogs")
        appbuilder.add_view(SalesReceiptModelView, "Sales Receipts", category="Sales")
        appbuilder.add_view(MysteryBoxesView, "Mystery Boxes", category="Sales")
        drafts = appbuilder.add_view(
            EbayDraftModelView, "eBay Drafts", category="Sales"
        )
        appbuilder.add_view(EbayListingModelView, "eBay Listings", category="Sales")
        appbuilder.add_view(EbayOrderModelView, "eBay Orders", category="Sales")
        discogs_listings = appbuilder.add_view(
            DiscogsListingModelView, "Discogs Listings", category="Sales"
        )
        appbuilder.add_view(DiscogsOrderModelView, "Discogs Orders", category="Sales")
        appbuilder.add_view(OtherItemModelView, "Other Items", category="Sales")
        appbuilder.add_view(ListingEndModelView, "Ended Listings", category="Sales")
        appbuilder.add_view(
            DescriptionTemplateView, "Description Template", category="Sales"
        )
        appbuilder.add_view(OrdersToShipView, "Orders to Ship (eBay)", category="Tasks")
        appbuilder.add_view(
            DiscogsOrdersToShipView, "Orders to Ship (Discogs)", category="Tasks"
        )
        appbuilder.add_view(
            FailedEndingsView, "Listings to End by Hand", category="Tasks"
        )
        appbuilder.add_view(ParkedListingsView, "Parked Listings", category="Tasks")
        appbuilder.add_view(
            ReleasesNeedingUnitsView, "Releases to Make Units", category="Tasks"
        )
        appbuilder.add_view(
            UnitsNeedingDraftsView, "Units to Draft (eBay)", category="Tasks"
        )
        appbuilder.add_link(
            "Drafts to List (eBay)",
            href=drafts.route_base + "/list/?" + urlencode(DRAFTS_TO_LIST_FILTERS),
            category="Tasks",
        )
        appbuilder.add_view(
            UnitsNeedingDiscogsDraftsView,
            "Units to Draft (Discogs)",
            category="Tasks",
        )
        appbuilder.add_link(
            "Drafts to List (Discogs)",
            href=discogs_listings.route_base
            + "/list/?"
            + urlencode(DISCOGS_DRAFTS_TO_LIST_FILTERS),
            category="Tasks",
        )
        appbuilder.add_view(SupplyModelView, "Supply List", category="Supplies")
        appbuilder.add_view(
            ReconcileSuppliesView, "Reconcile Supplies", category="Supplies"
        )
        appbuilder.add_view(
            PurchaseOrderModelView, "Purchase Orders", category="Supplies"
        )
        appbuilder.add_view_no_menu(PurchaseOrderSupplyModelView)
        appbuilder.add_view(SupplyUseModelView, "Supply Use", category="Supplies")
        appbuilder.add_view(ExpenseModelView, "Expenses", category="Supplies")
        appbuilder.add_view(ProfitAndLossView, "Profit & Loss", category="Reports")
        appbuilder.add_view(InventoryReportView, "Inventory", category="Reports")
        appbuilder.add_view(LotsReportView, "Lots", category="Reports")
        appbuilder.add_view(SalesReportView, "Sales", category="Reports")
        appbuilder.add_view(AgingReportView, "Aging", category="Reports")
        appbuilder.add_view(SuppliesReportView, "Supplies", category="Reports")
        appbuilder.add_view(ConnectEbayView, "Connect eBay", category="Connect")
        appbuilder.add_view(ConnectDiscogsView, "Connect Discogs", category="Connect")
