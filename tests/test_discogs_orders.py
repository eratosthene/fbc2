"""The Discogs orders sync: the seller's orders into DiscogsOrders."""

import datetime
import importlib.util
import logging
from pathlib import Path

import pytest
import sqlalchemy as sa
from fake_discogs import FakeAccount, FakeDiscogs, failing

from fbc import db
from fbc.discogs import client as discogs_client
from fbc.discogs.saving import save_discogs_listing
from fbc.models.discogs import DiscogsOrder, DiscogsOrderItem
from fbc.models.inventory import PurchaseLot, Unit
from fbc.core import localtime
from fbc.models.sync import SyncRecord
from fbc.sync import jobs, state

VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"


@pytest.fixture(autouse=True)
def info_logs(caplog):
    caplog.set_level(logging.INFO)


def _unit():
    lot = PurchaseLot(name="Lot", date=datetime.date(2024, 1, 1), price=10.0)
    unit = Unit(
        name="Unit", unit_type='12" LP', pressing="", grading="", purchase_lot=lot
    )
    db.session.add_all([lot, unit])
    db.session.commit()
    return unit


def _order(order_id="1-100", **changes):
    """One order, as GET /marketplace/orders has it."""
    order = {
        "id": order_id,
        "status": "New Order",
        "created": "2026-11-10T09:30:00-08:00",
        "last_activity": "2026-11-11T10:00:00-08:00",
        "buyer": {"username": "a_buyer", "id": 9},
        "fee": {"currency": "USD", "value": 1.92},
        "total": {"currency": "USD", "value": 24.0},
        "shipping": {"currency": "USD", "value": 5.0, "method": "Media Mail"},
        "items": [
            {
                "id": 7001,
                "price": {"currency": "USD", "value": 19.0},
                "media_condition": "Near Mint (NM or M-)",
                "sleeve_condition": "Very Good Plus (VG+)",
                "release": {"id": 555, "description": "An Artist - A Title"},
            }
        ],
    }
    order.update(changes)
    return order


@pytest.fixture
def orders(monkeypatch):
    """Set what Discogs's orders hold; returns the FakeAccount."""

    def hold(items, fail=None):
        return (
            FakeDiscogs(account=FakeAccount(orders=items, fail=fail))
            .install(monkeypatch)
            .account
        )

    return hold


def test_an_order_is_read_with_every_field(app, orders):
    account = orders([_order()])

    jobs.sync_discogs_orders()

    row = db.session.get(DiscogsOrder, "1-100")
    assert (row.status, row.buyer) == ("New Order", "a_buyer")
    assert row.created_time == datetime.datetime(2026, 11, 10, 17, 30)  # UTC
    assert row.last_activity == datetime.datetime(2026, 11, 11, 18, 0)
    assert (row.total, row.fee, row.shipping) == (24.0, 1.92, 5.0)
    assert (row.shipping_method, row.currency) == ("Media Mail", "USD")
    [item] = row.items
    assert (item.listing_id, item.release_id) == (7001, 555)
    assert (item.title, item.price) == ("An Artist - A Title", 19.0)
    assert item.media_condition == "Near Mint (NM or M-)"
    assert item.unit_id is None
    assert row.fmt_total() == "$24.00" and row.fmt_title() == "An Artist - A Title"
    assert account.names() == ["orders"]


def test_a_plain_sync_reads_30_days_and_a_deep_one_everything(app, orders):
    account = orders([])

    jobs.discogs_orders_job().fn()
    jobs.discogs_orders_job(deep=True).fn()

    plain, deep = account.calls_to("orders")
    age = localtime.utcnow() - plain[0]
    assert (
        datetime.timedelta(days=29, hours=23)
        < age
        < datetime.timedelta(days=30, minutes=5)
    )
    assert deep == (None,)


def test_an_item_links_to_the_unit_of_its_listing(app, orders):
    unit = _unit()
    save_discogs_listing(
        {
            "id": 7001,
            "status": "Sold",
            "release": {"id": 555},
            "external_id": f"fbc-{unit.id}",
        },
        datetime.datetime(2026, 11, 1),
    )
    orders([_order()])

    jobs.sync_discogs_orders()

    assert db.session.get(DiscogsOrder, "1-100").items[0].unit_id == unit.id


def test_a_second_sync_updates_the_status_and_keeps_the_items(app, orders):
    orders([_order()])
    jobs.sync_discogs_orders()
    orders([_order(status="Shipped", items=[])])

    jobs.sync_discogs_orders()

    assert db.session.query(DiscogsOrder).count() == 1
    row = db.session.get(DiscogsOrder, "1-100")
    assert row.status == "Shipped"
    assert db.session.query(DiscogsOrderItem).count() == 1


def test_an_order_with_several_items_and_missing_parts_is_kept(app, orders):
    item = {"id": 1, "release": {"id": 2, "description": "One"}}
    orders(
        [
            {
                "id": "1-5",
                "items": [item, {**item, "id": 2, "release": {"description": "Two"}}],
            }
        ]
    )

    jobs.sync_discogs_orders()

    row = db.session.get(DiscogsOrder, "1-5")
    assert (row.total, row.fee, row.buyer, row.created_time) == (None,) * 4
    assert row.fmt_title() == "One (+1 more)" and row.fmt_total() == ""
    assert len(row.items) == 2


def test_a_failed_read_saves_nothing_and_records_nothing(app, orders):
    orders(
        [_order()],
        fail=failing("orders", discogs_client.IncompleteRead("short read")),
    )

    with pytest.raises(discogs_client.IncompleteRead):
        jobs.sync_discogs_orders()

    assert db.session.query(DiscogsOrder).count() == 0
    assert SyncRecord.last_finished(jobs.DISCOGS_ORDERS_SYNC) is None


def test_a_finished_sync_records_when(app, orders):
    orders([])

    jobs.sync_discogs_orders()

    assert SyncRecord.last_finished(jobs.DISCOGS_ORDERS_SYNC) is not None


def test_the_sync_has_a_command(app, orders):
    orders([_order()])

    result = app.test_cli_runner().invoke(args=["sync", "discogs-orders", "--deep"])

    assert result.exit_code == 0, result.output
    assert "Discogs orders sync finished." in result.output
    assert db.session.get(DiscogsOrder, "1-100") is not None


def test_in_dev_mode_it_says_syncing_is_off_and_reaches_nothing(app, monkeypatch):
    monkeypatch.setitem(app.config, "DEV_MODE", True)

    def never():
        raise AssertionError("reached Discogs")

    monkeypatch.setattr(jobs.discogs_client, "account", never)

    result = app.test_cli_runner().invoke(args=["sync", "discogs-orders"])

    assert result.exit_code == 0, result.output
    assert "Dev Mode is turned on, external syncing disabled." in result.output
    assert db.session.query(DiscogsOrder).count() == 0


def test_the_buttons_have_routes(app, monkeypatch):
    monkeypatch.setattr(state, "_spawn", lambda target: None)

    client = app.test_client()
    assert client.get("/syncdiscogsorders").status_code != 404
    assert client.get("/syncdiscogsordersdeep").status_code != 404


@pytest.fixture
def admin_client(app):
    import fbc

    sm = fbc.appbuilder.sm
    sm.create_db()
    fbc.appbuilder.add_permissions(update_perms=True)
    sm.add_user("admin", "A", "Dmin", "admin@example.com", sm.find_role("Admin"), "pw")
    client = fbc.app.test_client()
    client.post("/login/", data={"username": "admin", "password": "pw"})
    return client


def test_the_list_and_show_pages_work_and_are_read_only(admin_client, orders):
    orders([_order()])
    jobs.sync_discogs_orders()

    listing = admin_client.get("/discogsordermodelview/list/")
    shown = admin_client.get("/discogsordermodelview/show/1-100")

    assert listing.status_code == 200
    assert b"An Artist - A Title" in listing.data
    assert b"a_buyer" in listing.data
    assert b"/syncdiscogsorders" in listing.data
    assert shown.status_code == 200
    assert admin_client.get("/discogsordermodelview/add").status_code in (302, 403, 404)
    assert admin_client.get("/discogsordermodelview/edit/1-100").status_code in (
        302,
        403,
        404,
    )


def test_the_migration_builds_the_tables_the_models_describe(db):
    spec = importlib.util.spec_from_file_location(
        "m", VERSIONS / "c3e7a1d94b58_add_discogs_order.py"
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        sa.Table(
            "unit", sa.MetaData(), sa.Column("id", sa.Integer, primary_key=True)
        ).create(conn)
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        inspector = sa.inspect(conn)
        for name in ("discogs_order", "discogs_order_item"):
            built = {c["name"]: c for c in inspector.get_columns(name)}
            indexed = {tuple(i["column_names"]) for i in inspector.get_indexes(name)}
            table = db.metadata.tables[name]
            assert set(built) == {c.name for c in table.columns}
            for column in table.columns:
                assert built[column.name]["nullable"] == column.nullable, column.name
            assert indexed == {(c.name,) for c in table.columns if c.index}


def test_the_listings_migration_is_still_the_one_before_it():
    text = (VERSIONS / "c3e7a1d94b58_add_discogs_order.py").read_text()

    assert 'down_revision = "b5d8f2a6c139"' in text
