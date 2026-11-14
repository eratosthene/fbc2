"""The Discogs listings sync: the seller inventory into DiscogsListings."""

import datetime
import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from fbc import db, sync, syncjobs
from fbc.models.discogs import DiscogsListing
from fbc.models.inventory import PurchaseLot, Unit
from fbc.util import save_discogs_listing

VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"


def _migration(name):
    spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def _item(listing_id=1, **changes):
    item = {
        "id": listing_id,
        "uri": f"https://www.discogs.com/sell/item/{listing_id}",
        "status": "For Sale",
        "price": {"value": 12.5, "currency": "USD"},
        "condition": "Near Mint (NM or M-)",
        "sleeve_condition": "Very Good Plus (VG+)",
        "posted": "2026-10-01T09:30:00-07:00",
        "external_id": None,
        "location": None,
        "release": {"id": 555, "description": "An Artist - A Title"},
    }
    item.update(changes)
    return item


class FakeInventory:
    """What `me.inventory` is: filter(), per_page, len(), pages and page(n)."""

    def __init__(self, items, per_page=2, lie_about_total=None):
        self.items = items
        self.per_page = per_page
        self.filters = {}
        self.total = len(items) if lie_about_total is None else lie_about_total

    def filter(self, **kwargs):
        self.filters = kwargs
        return self

    def __len__(self):
        return self.total

    @property
    def pages(self):
        return -(-len(self.items) // self.per_page)

    def page(self, n):
        chunk = self.items[(n - 1) * self.per_page : n * self.per_page]
        return [SimpleNamespace(data=item) for item in chunk]


@pytest.fixture
def inventory(monkeypatch):
    """Set what Discogs's inventory holds; returns the FakeInventory."""

    def hold(items, **kwargs):
        fake = FakeInventory(items, **kwargs)
        me = SimpleNamespace(inventory=fake)
        monkeypatch.setattr(
            syncjobs.discogs, "client", lambda: SimpleNamespace(identity=lambda: me)
        )
        return fake

    return hold


def test_a_listing_is_read_with_every_field(app, inventory):
    fake = inventory([_item(7, external_id="fbc-3", location="Box 4")])

    syncjobs.sync_discogs_listings()

    row = db.session.get(DiscogsListing, 7)
    assert (row.release_id, row.title) == (555, "An Artist - A Title")
    assert (row.price, row.currency, row.status) == (12.5, "USD", "For Sale")
    assert row.condition == "Near Mint (NM or M-)"
    assert row.sleeve_condition == "Very Good Plus (VG+)"
    assert row.posted == datetime.datetime(2026, 10, 1, 16, 30)  # UTC
    assert (row.external_id, row.location) == ("fbc-3", "Box 4")
    assert row.ended is False and row.last_seen is not None
    assert row.is_for_sale()
    # Discogs shows only For Sale items unless asked for All.
    assert fake.filters == {"status": "All"}
    assert fake.per_page == 100


def test_a_listing_links_to_its_unit_by_external_id(app, inventory):
    unit = _unit()
    inventory([_item(1, external_id=f"fbc-{unit.id}")])

    syncjobs.sync_discogs_listings()

    assert db.session.get(DiscogsListing, 1).unit_id == unit.id


@pytest.mark.parametrize(
    "external_id", [None, "", "my own note", "fbc-", "fbc-abc", "fbc-999", "12"]
)
def test_a_listing_without_a_usable_external_id_has_no_unit(
    app, inventory, external_id
):
    inventory([_item(1, external_id=external_id)])

    syncjobs.sync_discogs_listings()

    assert db.session.get(DiscogsListing, 1).unit_id is None


def test_a_release_that_isnt_in_the_collection_is_kept(app, inventory):
    inventory([_item(1, release={"id": 98765432, "description": "Not Mine"})])

    syncjobs.sync_discogs_listings()

    row = db.session.get(DiscogsListing, 1)
    assert row.release_id == 98765432 and row.release is None


def test_a_second_sync_updates_rather_than_adds(app, inventory):
    inventory([_item(1)])
    syncjobs.sync_discogs_listings()
    inventory([_item(1, status="Draft", price={"value": 9.0, "currency": "USD"})])

    syncjobs.sync_discogs_listings()

    assert db.session.query(DiscogsListing).count() == 1
    row = db.session.get(DiscogsListing, 1)
    assert (row.status, row.price) == ("Draft", 9.0)
    assert not row.is_for_sale()


def test_a_unit_stays_linked_when_the_external_id_is_later_changed(app, inventory):
    unit = _unit()
    inventory([_item(1, external_id=f"fbc-{unit.id}")])
    syncjobs.sync_discogs_listings()
    inventory([_item(1, external_id="edited by hand")])

    syncjobs.sync_discogs_listings()

    assert db.session.get(DiscogsListing, 1).unit_id == unit.id


def test_a_listing_that_left_the_inventory_is_marked_ended_and_can_return(
    app, inventory
):
    inventory([_item(1), _item(2), _item(3)])
    syncjobs.sync_discogs_listings()
    inventory([_item(1), _item(3)])

    syncjobs.sync_discogs_listings()

    ended = {r.listing_id: r.ended for r in db.session.query(DiscogsListing)}
    assert ended == {1: False, 2: True, 3: False}
    assert not db.session.get(DiscogsListing, 2).is_for_sale()
    inventory([_item(1), _item(2), _item(3)])
    syncjobs.sync_discogs_listings()
    assert db.session.get(DiscogsListing, 2).ended is False


@pytest.mark.parametrize(
    "status, ended",
    [
        ("For Sale", False),
        ("Draft", False),
        ("Expired", False),
        ("Sold", True),
        ("Deleted", True),
        ("Violation", True),
        ("Suspended", True),
    ],
)
def test_a_listing_that_cant_come_back_is_ended_even_though_it_is_listed(
    app, inventory, status, ended
):
    inventory([_item(1, status=status)])

    syncjobs.sync_discogs_listings()
    # A second sync, since the first one adds it and the second keeps it.
    syncjobs.sync_discogs_listings()

    row = db.session.get(DiscogsListing, 1)
    assert row.ended is ended
    assert row.is_for_sale() is (status == "For Sale")


def test_a_listing_synced_as_not_ended_before_it_sold_is_ended_by_the_next_sync(
    app, inventory
):
    inventory([_item(1)])
    syncjobs.sync_discogs_listings()
    inventory([_item(1, status="Sold")])

    syncjobs.sync_discogs_listings()

    assert db.session.get(DiscogsListing, 1).ended is True


def test_a_short_read_marks_nothing_ended_and_records_nothing(app, inventory):
    inventory([_item(1), _item(2)])
    syncjobs.sync_discogs_listings()
    sync.record_finished(syncjobs.DISCOGS_LISTINGS_SYNC, datetime.datetime(2020, 1, 1))
    inventory([_item(1)], lie_about_total=2)

    with pytest.raises(syncjobs.DiscogsInventoryError):
        syncjobs.sync_discogs_listings()

    assert not db.session.get(DiscogsListing, 2).ended
    assert sync.last_finished(syncjobs.DISCOGS_LISTINGS_SYNC) == datetime.datetime(
        2020, 1, 1
    )


def test_an_empty_inventory_is_fine(app, inventory):
    inventory([])

    syncjobs.sync_discogs_listings()

    assert db.session.query(DiscogsListing).count() == 0
    assert sync.last_finished(syncjobs.DISCOGS_LISTINGS_SYNC) is not None


def test_the_job_is_the_one_the_button_command_and_scheduler_share(app):
    from fbc import scheduler

    job = syncjobs.discogs_listings_job()

    assert job.kind == "discogs-listings"
    assert job.fn is syncjobs.sync_discogs_listings
    assert scheduler.JOBS["discogs-listings"] is syncjobs.discogs_listings_job
    assert scheduler.read_schedule(app.config).intervals["discogs-listings"]


def test_the_command_runs_the_job(app, inventory):
    inventory([_item(1)])

    result = app.test_cli_runner().invoke(args=["sync", "discogs-listings"])

    assert result.exit_code == 0, result.output
    assert "Discogs listings sync finished." in result.output
    assert db.session.get(DiscogsListing, 1) is not None


def test_the_button_has_a_route(app):
    # Not signed in, so it only has to exist.
    assert app.test_client().get("/syncdiscogslistings").status_code != 404


def test_in_dev_mode_it_says_syncing_is_off_and_reaches_nothing(
    app, monkeypatch, caplog
):
    monkeypatch.setitem(app.config, "DEV_MODE", True)

    def never():
        raise AssertionError("reached Discogs")

    monkeypatch.setattr(syncjobs.discogs, "client", never)

    result = app.test_cli_runner().invoke(args=["sync", "discogs-listings"])

    assert result.exit_code == 0, result.output
    assert "Dev Mode is turned on, external syncing disabled." in result.output
    assert db.session.query(DiscogsListing).count() == 0


def test_the_sync_only_reads_from_discogs(app, inventory):
    # The fake has no way to write: add_listing, save() and delete() don't
    # exist on it, so any attempt would fail the sync.
    inventory([_item(1)])

    syncjobs.sync_discogs_listings()

    assert db.session.get(DiscogsListing, 1) is not None


def test_the_migration_builds_the_table_the_model_describes(db):
    migration = _migration("b5d8f2a6c139_add_discogs_listing")
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        # Its foreign key's target.
        sa.Table(
            "unit", sa.MetaData(), sa.Column("id", sa.Integer, primary_key=True)
        ).create(conn)
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        inspector = sa.inspect(conn)
        built = {c["name"]: c for c in inspector.get_columns("discogs_listing")}
        indexed = {
            tuple(i["column_names"]) for i in inspector.get_indexes("discogs_listing")
        }

    table = db.metadata.tables["discogs_listing"]
    assert set(built) == {c.name for c in table.columns}
    for column in table.columns:
        assert built[column.name]["nullable"] == column.nullable, column.name
    assert indexed == {(c.name,) for c in table.columns if c.index}


def test_the_list_and_show_pages_work_and_are_read_only(app):
    import fbc

    sm = fbc.appbuilder.sm
    sm.create_db()
    fbc.appbuilder.add_permissions(update_perms=True)
    sm.add_user("admin", "A", "Dmin", "admin@example.com", sm.find_role("Admin"), "pw")
    client = fbc.app.test_client()
    client.post("/login/", data={"username": "admin", "password": "pw"})
    unit = _unit()
    save_discogs_listing(
        _item(5, external_id=f"fbc-{unit.id}"), datetime.datetime(2026, 11, 1)
    )

    listing = client.get("/discogslistingmodelview/list/")
    shown = client.get("/discogslistingmodelview/show/5")
    added = client.get("/discogslistingmodelview/add")

    assert listing.status_code == 200
    assert b"An Artist - A Title" in listing.data
    assert b"/syncdiscogslistings" in listing.data
    assert shown.status_code == 200
    assert b"fbc-" in shown.data
    assert added.status_code in (302, 403, 404)
