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


def test_figures_count_total_sold_and_in_stock(db):
    lot = _make_lot(db)
    _make_unit(db, lot, sold=True, name="Sold Unit")
    _make_unit(db, lot, sold=False, name="Unsold Unit 1")
    _make_unit(db, lot, sold=False, name="Unsold Unit 2")

    f = lot.figures()
    assert f["units"] == 3
    assert f["sold_units"] == 1
    assert f["instock"] == 2


def test_figures_with_no_units_is_all_zero(db):
    lot = _make_lot(db)
    f = lot.figures()
    assert (f["units"], f["sold_units"], f["breakeven"], f["perunit"]) == (0, 0, 0, 0)


def test_figures_with_no_sales_is_zero(db):
    lot = _make_lot(db, price=50.0)
    _make_unit(db, lot, sold=False)

    f = lot.figures()
    assert f["gross"] == 0
    assert f["net"] == 0
    assert f["fees"] == 0.0
    assert f["feepc"] == 0.0
    assert f["profit"] == -50.0
    assert f["roi"] == -100.0


def test_figures_with_a_free_lot_has_no_roi(db):
    lot = _make_lot(db, price=0.0)
    _make_unit(db, lot)
    assert lot.figures()["roi"] == 0.0


def test_figures_aggregate_sales_receipts(db):
    lot = _make_lot(db, price=50.0)
    receipt = SalesReceipt(
        date=datetime.date(2024, 2, 1), sold_price=100.0, net_sold=90.0
    )
    db.session.add(receipt)
    db.session.commit()
    _make_unit(db, lot, sold=True, sales_receipt=receipt)

    f = lot.figures()
    assert f["gross"] == 100.0
    assert f["net"] == 90.0
    assert f["fees"] == 10.0
    assert f["feepc"] == 10.0
    assert f["profit"] == 40.0
    assert f["roi"] == 80.0


def test_figures_forecast_breakeven_and_projected_profit(db):
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

    f = lot.figures()
    assert f["breakeven"] == pytest.approx(100.0 / 2 / 0.9)
    assert f["avg_sold_price"] == 60.0
    assert f["potential_profit"] == pytest.approx(120.0 - 12.0 - 100.0)
    assert f["avg_profit_per_unit"] == pytest.approx(60.0 - f["breakeven"])
    assert f["likely_profit"] == pytest.approx(2 * f["avg_profit_per_unit"])


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


def test_unit_discogs_genre_and_style_columns_list_each_separately(app, db):
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
    release.styles = [Style(name="House"), Style(name="Disco")]
    db.session.add(release)
    db.session.commit()
    unit = Unit(name="Unit", discogs_release=release)
    assert str(unit.discogs_genre_column()) == "Electronic"
    assert str(unit.discogs_style_column()) == "House, Disco"


def test_unit_discogs_genre_and_style_columns_empty_without_release(app):
    unit = Unit(name="Unit")
    assert str(unit.discogs_genre_column()) == ""
    assert str(unit.discogs_style_column()) == ""


def _sold_unit(db, master, sold_price=None, ebay_order=None, listing_id=None):
    receipt = None
    if sold_price is not None or ebay_order is not None:
        receipt = SalesReceipt(
            date=datetime.date(2024, 1, 1),
            sold_price=sold_price,
            net_sold=sold_price,
            ebay_orders=[ebay_order] if ebay_order else [],
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
