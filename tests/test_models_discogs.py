import datetime
import json

from fbc.models.discogs import (
    Artist,
    DiscogsInstance,
    DiscogsRelease,
    Folder,
    Genre,
    Style,
)


def test_artist_repr_uses_name_when_no_sort_name():
    artist = Artist(artist_id=1, name="Daft Punk")
    assert repr(artist) == "Daft Punk"


def test_artist_repr_prefers_sort_name():
    artist = Artist(artist_id=2, name="The Beatles", sort_name="Beatles, The")
    assert repr(artist) == "Beatles, The"


def test_genre_repr_returns_name():
    assert repr(Genre(name="Rock")) == "Rock"


def test_style_repr_returns_name():
    assert repr(Style(name="Punk")) == "Punk"


def test_folder_repr_returns_name():
    assert repr(Folder(folder_id=1, name="Records")) == "Records"


def test_discogs_instance_repr_returns_instance_id():
    assert repr(DiscogsInstance(instance_id=123456789)) == "123456789"


def _make_release(**overrides):
    defaults = {
        "release_id": 100,
        "title": "Random Access Memories",
        "year": 2013,
        "master_id": 0,
        "master_year": 2013,
        "formats": json.dumps([{"name": "Vinyl"}]),
        "released": "2013-05-17",
        "folder_id": None,
        "notes": json.dumps([]),
        "date_added": datetime.date(2020, 1, 1),
    }
    defaults.update(overrides)
    return DiscogsRelease(**defaults)


def test_artist_rep_joins_multiple_artists_with_slash():
    release = _make_release()
    release.artists = [Artist(artist_id=1, name="Daft Punk")]
    assert release.artist_rep() == "Daft Punk"

    release.artists = [
        Artist(artist_id=1, name="Daft Punk"),
        Artist(artist_id=2, name="Pharrell Williams"),
    ]
    assert release.artist_rep() == "Daft Punk / Pharrell Williams"


def test_artist_rep_empty_when_no_artists():
    release = _make_release()
    release.artists = []
    assert release.artist_rep() == ""


def test_unicode_and_repr_combine_artist_title_and_year():
    release = _make_release(title="Discovery", year=2001)
    release.artists = [Artist(artist_id=1, name="Daft Punk")]
    assert repr(release) == "Daft Punk - Discovery (2001)"
    assert release.__unicode__() == "Daft Punk - Discovery (2001)"


def test_release_show_links_to_discogs_release():
    release = _make_release(release_id=100)
    html = release.release_show(markup=False)
    assert 'href="https://www.discogs.com/release/100"' in html
    assert "D:Release" in html


def test_master_show_links_to_discogs_master():
    release = _make_release(master_id=200)
    html = release.master_show(markup=False)
    assert 'href="https://www.discogs.com/master/200"' in html
    assert "D:Master" in html


def test_filter_master_uses_master_id_when_present(app):
    release = _make_release(release_id=100, master_id=200)
    html = release.filter_master(markup=False)
    assert "_flt_3_discogs_master=200" in html
    assert "Filter:Master" in html


def test_filter_master_falls_back_to_release_id_when_no_master(app):
    release = _make_release(release_id=100, master_id=0)
    html = release.filter_master(markup=False)
    assert "_flt_3_discogs_master=r100" in html


def test_filter_master_falls_back_when_master_id_is_none(app):
    release = _make_release(release_id=100, master_id=None)
    html = release.filter_master(markup=False)
    assert "_flt_3_discogs_master=r100" in html


def test_unit_list_links_to_units_filtered_by_release(app):
    release = _make_release(release_id=100)
    html = release.unit_list(markup=False)
    assert "_flt_0_discogs_release=100" in html
    assert "List Units" in html


def test_unit_add_extracts_grading_from_notes(app):
    notes = json.dumps(
        [
            {"field_id": 1, "value": "Media (Near Mint (NM or M-))"},
            {"field_id": 2, "value": "Sleeve (Very Good Plus (VG+))"},
        ]
    )
    release = _make_release(release_id=100, master_id=200, notes=notes)
    html = release.unit_add(instance_id=999, markup=False)
    assert "grading=NM+or+M-" in html
    assert "VG%2B" in html
    assert "discogs_master=200" in html
    assert "999" in html


def test_unit_add_uses_r_prefixed_master_when_no_master(app):
    release = _make_release(release_id=100, master_id=0, notes=json.dumps([]))
    html = release.unit_add(instance_id=1, markup=False)
    assert "discogs_master=r100" in html


def test_link_column_combines_all_sub_links(app):
    release = _make_release(release_id=100, master_id=200, notes=json.dumps([]))
    release.artists = []
    release.discogs_instances = [DiscogsInstance(instance_id=42)]
    html = str(release.link_column())
    assert "D:Release" in html
    assert "D:Master" in html
    assert "Filter:Master" in html
    assert "List Units" in html
    assert "Add Unit" in html


def test_purchase_lot_returns_empty_when_no_matching_unit(app, db):
    release = _make_release(release_id=100)
    db.session.add(release)
    db.session.commit()

    assert str(release.purchase_lot()) == ""


def test_purchase_lot_returns_lot_name_of_matching_unit(app, db):
    from fbc.models.inventory import PurchaseLot, Unit

    release = _make_release(release_id=100)
    lot = PurchaseLot(name="Lot A", date=datetime.date(2024, 1, 1), price=10.0)
    db.session.add_all(
        [release, Unit(name="With Lot", discogs_release=release, purchase_lot=lot)]
    )
    db.session.commit()

    assert str(release.purchase_lot()) == "Lot A"


def test_purchase_lot_returns_empty_when_unit_has_no_lot(app, db):
    from fbc.models.inventory import Unit

    release = _make_release(release_id=100)
    db.session.add_all([release, Unit(name="No Lot", discogs_release=release)])
    db.session.commit()

    assert str(release.purchase_lot()) == ""


def _add_sold_unit(db, master, sold_price):
    from fbc.models.inventory import Unit
    from fbc.models.sales import SalesReceipt

    receipt = SalesReceipt(
        date=datetime.date(2024, 1, 1), sold_price=sold_price, net_sold=sold_price
    )
    db.session.add(
        Unit(name="Sold", discogs_master=master, sold=True, sales_receipt=receipt)
    )
    db.session.commit()


def test_unit_add_prefills_retail_price_from_sold_units_of_same_master(app, db):
    _add_sold_unit(db, "200", 20.0)
    _add_sold_unit(db, "200", 31.0)
    release = _make_release(release_id=100, master_id=200)
    html = release.unit_add(instance_id=1, markup=False)
    assert "retail_price=25.50" in html


def test_unit_add_prefills_retail_price_for_masterless_release(app, db):
    _add_sold_unit(db, "r100", 15.0)
    release = _make_release(release_id=100, master_id=0)
    html = release.unit_add(instance_id=1, markup=False)
    assert "retail_price=15.00" in html


def test_unit_add_omits_retail_price_when_nothing_sold(app, db):
    release = _make_release(release_id=100, master_id=200)
    html = release.unit_add(instance_id=1, markup=False)
    assert "retail_price" not in html


def test_unit_add_handles_none_master_id(app, db):
    release = _make_release(release_id=100, master_id=None)
    html = release.unit_add(instance_id=1, markup=False)
    assert "discogs_master=r100" in html


def test_add_unit_form_prefills_retail_price_from_query_args(app):
    from fbc import appbuilder

    view = next(v for v in appbuilder.baseviews if type(v).__name__ == "UnitModelView")
    with app.test_request_context("/?retail_price=25.50"):
        form = view.add_form.refresh()
        assert form.retail_price.data == 25.5
