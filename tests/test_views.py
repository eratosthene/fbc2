from werkzeug.datastructures import MultiDict

import fbc
from fbc.views.discogs import ArtistModelView, DiscogsReleaseModelView, FolderModelView
from fbc.views.sales import EbayListingModelView, EbayOrderModelView


def _view(view_class):
    for view in fbc.appbuilder.baseviews:
        if isinstance(view, view_class):
            return view
    raise AssertionError(f"{view_class.__name__} is not registered")


def test_artist_add_form_includes_primary_key():
    view = _view(ArtistModelView)
    form = view.add_form(
        MultiDict({"artist_id": "1", "name": "Some Artist", "sort_name": ""})
    )
    assert form.validate(), form.errors
    assert form.artist_id.data == 1


def test_folder_add_form_includes_primary_key():
    view = _view(FolderModelView)
    form = view.add_form(MultiDict({"folder_id": "1", "name": "Some Folder"}))
    assert form.validate(), form.errors
    assert form.folder_id.data == 1


def test_discogs_release_add_form_includes_primary_key():
    view = _view(DiscogsReleaseModelView)
    form = view.add_form(
        MultiDict(
            {
                "release_id": "1",
                "title": "Some Album",
                "year": "2020",
                "master_id": "0",
                "master_year": "2020",
                "released": "2020-01-01",
            }
        )
    )
    assert form.validate(), form.errors
    assert form.release_id.data == 1


def test_ebay_listing_add_form_accepts_full_datetime():
    view = _view(EbayListingModelView)
    form = view.add_form(
        MultiDict(
            {
                "item_id": "ITEM-1",
                "title": "Some Listing",
                "price": "9.99",
                "url": "https://example.com",
                "start_time": "2024-02-01T09:30:00",
            }
        )
    )
    assert form.validate(), form.errors
    assert form.start_time.data.isoformat() == "2024-02-01T09:30:00"


def test_ebay_listing_add_form_allows_blank_start_time():
    view = _view(EbayListingModelView)
    form = view.add_form(
        MultiDict(
            {
                "item_id": "ITEM-1",
                "title": "Some Listing",
                "price": "9.99",
                "url": "https://example.com",
                "start_time": "",
            }
        )
    )
    assert form.validate(), form.errors
    assert form.start_time.data is None


def test_ebay_order_add_form_accepts_full_datetime():
    view = _view(EbayOrderModelView)
    form = view.add_form(
        MultiDict(
            {
                "order_id": "ORD-1",
                "created_time": "2024-01-15T13:45:00",
                "buyer": "someone",
                "price": "12.50",
                "title": "Some Item",
            }
        )
    )
    assert form.validate(), form.errors
    assert form.created_time.data.isoformat() == "2024-01-15T13:45:00"
