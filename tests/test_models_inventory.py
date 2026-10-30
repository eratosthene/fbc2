import datetime

import pytest

from fbc.models.discogs import DiscogsRelease
from fbc.models.ebay import EbayListing
from fbc.models.inventory import PurchaseLot, StorageBox, Unit
from fbc.models.sales import SalesReceipt


def _make_lot(db, price=100.0, name="Test Lot"):
    lot = PurchaseLot(name=name, date=datetime.date(2024, 1, 1), price=price)
    db.session.add(lot)
    db.session.commit()
    return lot


def _make_unit(db, lot, retail_price=None, sold=False, sales_receipt=None, name="Unit"):
    unit = Unit(
        name=name,
        unit_type='12" LP',
        pressing="",
        grading="",
        purchase_lot=lot,
        retail_price=retail_price,
        sold=sold,
        sales_receipt=sales_receipt,
    )
    db.session.add(unit)
    db.session.commit()
    return unit


def test_storage_box_repr_returns_name():
    assert repr(StorageBox(name="Box A")) == "Box A"


def test_purchase_lot_repr_returns_name():
    assert repr(PurchaseLot(name="Spring Haul")) == "Spring Haul"


def test_purchase_lot_purchase_price_formats_currency():
    lot = PurchaseLot(name="Lot", date=datetime.date(2024, 1, 1), price=42.5)
    assert lot.purchase_price() == "$42.50"


def test_compute_units_counts_total_sold_and_left(db):
    lot = _make_lot(db)
    _make_unit(db, lot, sold=True, name="Sold Unit")
    _make_unit(db, lot, sold=False, name="Unsold Unit 1")
    _make_unit(db, lot, sold=False, name="Unsold Unit 2")

    units = lot.compute_units()
    assert units["total"] == 3
    assert units["sold"] == 1
    assert units["left"] == 2


def test_compute_units_with_no_units_is_all_zero(db):
    lot = _make_lot(db)
    units = lot.compute_units()
    assert units == {"total": 0, "sold": 0, "left": 0}


def test_compute_profit_with_no_sales_is_zero(db):
    lot = _make_lot(db, price=50.0)
    _make_unit(db, lot, sold=False)

    profit = lot.compute_profit()
    assert profit["gross"] == 0.0
    assert profit["net"] == 0.0
    assert profit["fees"] == 0.0
    assert profit["feepc"] == 0.0
    assert profit["profit"] == -50.0
    assert profit["roi"] == -1.0


def test_compute_profit_aggregates_sales_receipts(db):
    lot = _make_lot(db, price=50.0)
    receipt = SalesReceipt(
        date=datetime.date(2024, 2, 1), sold_price=100.0, net_sold=90.0
    )
    db.session.add(receipt)
    db.session.commit()
    _make_unit(db, lot, sold=True, sales_receipt=receipt)

    profit = lot.compute_profit()
    assert profit["gross"] == 100.0
    assert profit["net"] == 90.0
    assert profit["fees"] == 10.0
    assert profit["feepc"] == 0.1
    assert profit["profit"] == 40.0
    assert profit["roi"] == 0.8


def test_compute_forecast_breakeven_and_projected_profit(db):
    lot = _make_lot(db, price=100.0)
    receipt = SalesReceipt(
        date=datetime.date(2024, 2, 1), sold_price=60.0, net_sold=54.0
    )
    db.session.add(receipt)
    db.session.commit()
    _make_unit(
        db, lot, retail_price=60.0, sold=True, sales_receipt=receipt, name="Sold"
    )
    _make_unit(db, lot, retail_price=60.0, sold=False, name="Unsold")

    t = lot.compute_profit()
    u = lot.compute_units()
    forecast = lot.compute_forecast(t, u)

    assert forecast["breakeven"] == pytest.approx(100.0 / 2 / (1 - t["feepc"]))
    assert forecast["avgsoldprice"] == 60.0
    assert forecast["avgppu"] == pytest.approx(60.0 - forecast["breakeven"])
    assert forecast["lprofit"] == pytest.approx(2 * forecast["avgppu"])


def test_compute_forecast_with_no_units_has_zero_breakeven(db):
    lot = _make_lot(db, price=100.0)
    t = lot.compute_profit()
    u = lot.compute_units()
    forecast = lot.compute_forecast(t, u)
    assert forecast["breakeven"] == 0
    assert forecast["avgsoldprice"] == 0


def test_list_sold_total_and_profit_helpers(db):
    lot = _make_lot(db, price=50.0)
    receipt = SalesReceipt(
        date=datetime.date(2024, 2, 1), sold_price=100.0, net_sold=90.0
    )
    db.session.add(receipt)
    db.session.commit()
    _make_unit(db, lot, sold=True, sales_receipt=receipt, name="Sold")
    _make_unit(db, lot, sold=False, name="Unsold")

    assert lot.list_sold() == 1
    assert lot.list_total() == 2
    assert lot.list_profit() == "$40.00"


def test_breakdown_renders_html_tables(db):
    lot = _make_lot(db, price=50.0)
    _make_unit(db, lot, sold=False)
    html = str(lot.breakdown())
    assert "<table" in html
    assert "Capital" in html
    assert "Breakeven Price" in html


def test_unit_repr_combines_fields():
    unit = Unit(name="Discovery", unit_type='12" LP', pressing="US", grading="NM")
    assert repr(unit) == 'Discovery 12" LP US NM'


def test_unit_fmt_retail_price_formats_when_set():
    unit = Unit(name="Unit", retail_price=19.99)
    assert unit.fmt_retail_price() == "$19.99"


def test_unit_fmt_retail_price_none_when_unset():
    unit = Unit(name="Unit", retail_price=None)
    assert unit.fmt_retail_price() is None


def test_unit_master_show_links_to_master_when_numeric(app):
    unit = Unit(name="Unit", discogs_master="12345")
    html = unit.master_show(markup=False)
    assert "_flt_3_discogs_master=12345" in html
    assert "https://www.discogs.com/master/12345" in html
    assert "D:Master" in html


def test_unit_master_show_links_to_release_when_no_master(app):
    unit = Unit(name="Unit", discogs_master="r6789")
    html = unit.master_show(markup=False)
    assert "https://www.discogs.com/release/6789" in html
    assert "D:Release(NM)" in html


def test_unit_link_column_includes_discogs_release(app, db):
    release = DiscogsRelease(
        release_id=1,
        title="Discovery",
        year=2001,
        formats="[]",
        released="2001",
        notes="[]",
    )
    db.session.add(release)
    db.session.commit()
    unit = Unit(name="Unit", discogs_release=release)
    html = str(unit.link_column())
    assert "D:Release" in html


def test_unit_link_column_includes_ebay_listing(app, db):
    listing = EbayListing(
        item_id="123", title="Listing", price=9.99, url="https://ebay.com/x"
    )
    db.session.add(listing)
    db.session.commit()
    unit = Unit(name="Unit", ebay_listing=listing)
    html = str(unit.link_column())
    assert "eBay:Listing" in html
    assert "$9.99" in html


def test_unit_link_column_empty_when_no_relations(app):
    unit = Unit(name="Unit")
    assert str(unit.link_column()) == ""


def test_unit_discogs_genre_column_merges_genres_and_styles(app, db):
    from fbc.models.discogs import Genre, Style

    release = DiscogsRelease(
        release_id=1,
        title="Discovery",
        year=2001,
        formats="[]",
        released="2001",
        notes="[]",
    )
    release.genres = [Genre(name="Electronic")]
    release.styles = [Style(name="House")]
    db.session.add(release)
    db.session.commit()
    unit = Unit(name="Unit", discogs_release=release)
    genre_column = str(unit.discogs_genre_column())
    assert "Electronic" in genre_column
    assert "House" in genre_column


def test_unit_discogs_genre_column_empty_without_release(app):
    unit = Unit(name="Unit")
    assert str(unit.discogs_genre_column()) == ""


def _sold_unit(db, master, sold_price=None, ebay_order=None, listing_id=None):
    receipt = None
    if sold_price is not None or ebay_order is not None:
        receipt = SalesReceipt(
            date=datetime.date(2024, 1, 1),
            sold_price=sold_price,
            net_sold=sold_price,
            ebay_order=ebay_order,
        )
    unit = Unit(
        name="Unit",
        unit_type='12" LP',
        pressing="",
        grading="",
        discogs_master=master,
        ebay_listing_id=listing_id,
        sold=True,
        sales_receipt=receipt,
    )
    db.session.add(unit)
    db.session.commit()
    return unit


def _ebay_order(db, order_id, price, listing_id=None):
    from fbc.models.ebay import EbayOrder

    order = EbayOrder(
        order_id=order_id,
        created_time=datetime.datetime(2024, 1, 1),
        buyer="buyer",
        price=price,
        ebay_listing_id=listing_id,
    )
    db.session.add(order)
    db.session.commit()
    return order


def test_average_sold_price_averages_sales_receipts_for_master(db):
    _sold_unit(db, "200", sold_price=20.0)
    _sold_unit(db, "200", sold_price=25.0)
    _sold_unit(db, "999", sold_price=100.0)

    assert Unit.average_sold_price("200") == 22.5


def test_average_sold_price_ignores_unsold_units(db):
    _sold_unit(db, "200", sold_price=20.0)
    db.session.add(
        Unit(name="Unsold", discogs_master="200", sold=False, retail_price=99.0)
    )
    db.session.commit()

    assert Unit.average_sold_price("200") == 20.0


def test_average_sold_price_falls_back_to_receipt_ebay_order(db):
    order = _ebay_order(db, "111", 30.0)
    _sold_unit(db, "200", sold_price=None, ebay_order=order)
    _sold_unit(db, "200", sold_price=10.0)

    assert Unit.average_sold_price("200") == 20.0


def test_average_sold_price_falls_back_to_listing_ebay_order(db):
    db.session.add(EbayListing(item_id="L1", title="T", price=40.0, url="https://x"))
    db.session.commit()
    _ebay_order(db, "222", 40.0, listing_id="L1")
    _sold_unit(db, "200", listing_id="L1")

    assert Unit.average_sold_price("200") == 40.0


def test_average_sold_price_is_none_when_nothing_sold(db):
    db.session.add(Unit(name="Unsold", discogs_master="200", sold=False))
    db.session.commit()

    assert Unit.average_sold_price("200") is None


def test_average_sold_price_rounds_to_cents(db):
    for price in (10.0, 10.0, 10.01):
        _sold_unit(db, "200", sold_price=price)

    assert Unit.average_sold_price("200") == 10.0
