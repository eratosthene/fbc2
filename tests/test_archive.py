import datetime

import pytest

import fbc
from fbc import archive, finance
from fbc.index import home_totals
from fbc.models.ebay import EbayListing, EbayOrder
from fbc.models.inventory import PurchaseLot, Unit
from fbc.models.sales import SalesReceipt, SalesReceiptLine, earnings_totals

NOW = datetime.datetime(2026, 11, 6, 12, 0)
OLD = datetime.datetime(2025, 6, 1, 9, 0)
RECENT = datetime.datetime(2026, 8, 1, 9, 0)


@pytest.fixture
def client():
    """A test client logged in as an Admin."""
    sm = fbc.appbuilder.sm
    sm.create_db()
    fbc.appbuilder.add_permissions(update_perms=True)
    sm.add_user("admin", "A", "Dmin", "admin@example.com", sm.find_role("Admin"), "pw")
    client = fbc.app.test_client()
    client.post("/login/", data={"username": "admin", "password": "pw"})
    return client


def _unit(name, **kwargs):
    unit = Unit(name=name, unit_type='12" LP', pressing="", grading="", **kwargs)
    fbc.db.session.add(unit)
    return unit


def _receipt(date, **kwargs):
    receipt = SalesReceipt(date=date.date(), sold_price=20.0, net_sold=17.0, **kwargs)
    fbc.db.session.add(receipt)
    return receipt


def _run(apply=True, config=None):
    fbc.db.session.flush()
    counts = archive.archive_all(apply=apply, config=config or {}, now=NOW, echo=str)
    fbc.db.session.expire_all()
    return counts


def test_cutoffs_use_the_configured_days_and_skip_zero_and_none():
    config = {
        "ARCHIVE_AFTER_DAYS": {"UNITS": 30, "EBAY_ORDERS": 0, "EBAY_LISTINGS": None}
    }

    cutoff = archive.cutoffs(config, NOW)

    assert cutoff["UNITS"] == NOW - datetime.timedelta(days=30)
    assert cutoff["SALES_RECEIPTS"] == NOW - datetime.timedelta(days=365)
    assert "EBAY_ORDERS" not in cutoff
    assert "EBAY_LISTINGS" not in cutoff


def test_a_sold_unit_is_dated_by_its_receipt_and_an_unsold_one_never_archived():
    old = _unit("old", sold=True, sales_receipt=_receipt(OLD))
    recent = _unit("recent", sold=True, sales_receipt=_receipt(RECENT))
    unsold = _unit("unsold", sold=False, sales_receipt=_receipt(OLD))

    counts = _run()

    assert counts["UNITS"] == 1
    assert old.archived is True
    assert recent.archived is False
    assert unsold.archived is False


def test_a_sold_unit_with_no_receipt_is_dated_by_its_listings_first_order():
    listing = EbayListing(item_id="1", title="t", price=5.0, start_time=OLD)
    fbc.db.session.add(listing)
    fbc.db.session.add_all(
        [
            EbayOrder(
                order_id="o1",
                created_time=OLD,
                buyer="b",
                price=5.0,
                ebay_listing=listing,
            ),
            EbayOrder(
                order_id="o2",
                created_time=RECENT,
                buyer="b",
                price=5.0,
                ebay_listing=listing,
            ),
        ]
    )
    dated = _unit("dated", sold=True, ebay_listing=listing)
    undated = _unit("undated", sold=True)

    _run()

    assert dated.archived is True
    assert undated.archived is False


def test_a_listing_is_archived_once_old_and_all_its_units_are_sold():
    def listing(item_id, start, *sold):
        entry = EbayListing(item_id=item_id, title="t", price=5.0, start_time=start)
        fbc.db.session.add(entry)
        for i, flag in enumerate(sold):
            _unit(item_id + str(i), sold=flag, ebay_listing=entry)
        return entry

    done = listing("1", OLD, True, True)
    part_sold = listing("2", OLD, True, False)
    recent = listing("3", RECENT, True)
    no_units = listing("4", OLD)

    _run()

    assert [done.archived, part_sold.archived, recent.archived, no_units.archived] == [
        True,
        False,
        False,
        False,
    ]


def _order(order_id, created, receipt=None):
    order = EbayOrder(order_id=order_id, created_time=created, buyer="b", price=5.0)
    if receipt:
        order.sales_receipts.append(receipt)
    fbc.db.session.add(order)
    return order


def test_an_order_needs_to_be_old_shipped_and_have_nothing_held():
    shipped = _order("shipped", OLD, _receipt(OLD))
    unshipped = _order("unshipped", OLD)
    recent = _order("recent", RECENT, _receipt(RECENT))
    held_receipt = _receipt(OLD)
    held_receipt.lines = [
        SalesReceiptLine(kind="SALE", label="Sale", amount=20.0),
        SalesReceiptLine(
            kind="DISPUTE", label="Hold", amount=-20.0, transaction_id="DISPUTE_HOLD-1"
        ),
    ]
    held = _order("held", OLD, held_receipt)
    released_receipt = _receipt(OLD)
    released_receipt.lines = [
        SalesReceiptLine(
            kind="DISPUTE", label="Hold", amount=-20.0, transaction_id="DISPUTE_HOLD-2"
        ),
        SalesReceiptLine(
            kind="CREDIT",
            label="Released",
            amount=20.0,
            transaction_id="DISPUTE_HOLD_REVERSE-2-1",
        ),
    ]
    released = _order("released", OLD, released_receipt)

    _run()

    assert shipped.archived is True
    assert released.archived is True
    assert [unshipped.archived, recent.archived, held.archived] == [False] * 3


def test_a_receipt_is_archived_when_old_enough():
    old, recent = _receipt(OLD), _receipt(RECENT)

    counts = _run(config={"ARCHIVE_AFTER_DAYS": {"SALES_RECEIPTS": 100}})

    assert old.archived is True
    assert recent.archived is False
    assert counts["SALES_RECEIPTS"] == 1


def test_a_dry_run_changes_nothing_and_a_rerun_finds_nothing_more():
    receipt = _receipt(OLD)

    counts = _run(apply=False)
    assert counts["SALES_RECEIPTS"] == 1
    assert receipt.archived is False

    _run()
    assert receipt.archived is True
    assert _run()["SALES_RECEIPTS"] == 0


def test_archiving_changes_no_money_figure():
    lot = PurchaseLot(name="Lot", date=datetime.date(2025, 1, 1), price=50.0)
    fbc.db.session.add(lot)
    receipt = _receipt(OLD)
    _unit("sold", sold=True, sales_receipt=receipt, purchase_lot=lot)
    _unit("left", sold=False, purchase_lot=lot, retail_price=30.0)
    fbc.db.session.commit()
    before = (
        finance.lot_figures()[lot.id],
        home_totals(),
        earnings_totals(),
    )

    counts = _run()

    assert counts["UNITS"] == 1 and counts["SALES_RECEIPTS"] == 1
    assert (finance.lot_figures()[lot.id], home_totals(), earnings_totals()) == before


def test_lists_hide_archived_by_default_and_can_show_all_or_only_archived(client):
    _receipt(RECENT)
    gone = _receipt(OLD, archived=True)
    fbc.db.session.commit()

    def ids(url):
        page = client.get(url).get_data(as_text=True)
        return "/salesreceiptmodelview/show/" + str(gone.id) in page, page

    hidden, page = ids("/salesreceiptmodelview/list/")
    assert not hidden
    assert "Show archived" in page
    shown, page = ids("/salesreceiptmodelview/list/?archived=all")
    assert shown
    assert "Hide archived" in page
    only, _ = ids("/salesreceiptmodelview/list/?_flt_0_archived=y")
    assert only


def test_related_lists_still_show_archived_units(client):
    lot = PurchaseLot(name="Lot", date=datetime.date(2025, 1, 1), price=50.0)
    fbc.db.session.add(lot)
    _unit("Hidden Away", sold=True, archived=True, purchase_lot=lot)
    fbc.db.session.commit()

    assert "Hidden Away" not in client.get("/unitmodelview/list/").get_data(
        as_text=True
    )
    assert "Hidden Away" in client.get(
        "/purchaselotmodelview/show/" + str(lot.id)
    ).get_data(as_text=True)


def test_archive_and_unarchive_actions_set_it_by_hand(client):
    receipt = _receipt(RECENT)
    fbc.db.session.commit()

    client.post(
        "/salesreceiptmodelview/action_post",
        data={"action": "archive", "rowid": str(receipt.id)},
    )
    fbc.db.session.expire_all()
    assert receipt.archived is True

    client.post(
        "/salesreceiptmodelview/action_post",
        data={"action": "unarchive", "rowid": str(receipt.id)},
    )
    fbc.db.session.expire_all()
    assert receipt.archived is False


def test_the_archive_command_is_a_dry_run_without_apply(app):
    receipt = _receipt(datetime.datetime(2000, 1, 1))
    fbc.db.session.commit()

    result = app.test_cli_runner().invoke(args=["archive"])
    assert result.exception is None, result.output
    assert "dry run" in result.output
    fbc.db.session.expire_all()
    assert receipt.archived is False

    result = app.test_cli_runner().invoke(args=["archive", "--apply"])
    assert "Archived 1 sales receipts" in result.output
    fbc.db.session.expire_all()
    assert receipt.archived is True
