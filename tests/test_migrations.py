import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"


def _migration(name):
    spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_null_unit_discogs_master_and_sold_become_blank_and_not_null():
    migration = _migration("f1b7c4e2a9d3_make_unit_discogs_master_and_sold_not_null")
    engine = sa.create_engine("sqlite://")
    unit = sa.Table(
        "unit",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("discogs_master", sa.String(21), index=True),
        sa.Column("sold", sa.Boolean, index=True),
    )
    with engine.begin() as conn:
        unit.create(conn)
        conn.execute(unit.insert().values(id=1))
        conn.execute(unit.insert().values(id=2, discogs_master="123", sold=True))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()

        rows = conn.execute(sa.text("SELECT id, discogs_master, sold FROM unit"))
        assert sorted(rows) == [(1, "", False), (2, "123", True)]
        conn.execute(sa.text("INSERT INTO unit (id) VALUES (3)"))
        new = conn.execute(
            sa.text("SELECT discogs_master, sold FROM unit WHERE id = 3")
        )
        assert tuple(new.one()) == ("", False)
        for column in ("discogs_master", "sold"):
            with pytest.raises(sa.exc.IntegrityError):
                conn.execute(
                    sa.text(f"INSERT INTO unit (id, {column}) VALUES (4, NULL)")
                )


def test_supplies_get_their_usage_from_their_names():
    migration = _migration("a7c3e9f1d2b4_add_supply_usage_and_reorder_level")
    engine = sa.create_engine("sqlite://")
    supply = sa.Table(
        "supply",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
    )
    with engine.begin() as conn:
        supply.create(conn)
        for i, name in enumerate(
            [
                "LP Mailers",
                'Vinyl Outer Sleeves, 7"',
                "Shipping Label Roll",
                "Packing Tape Rolls",
                'Vinyl Inner Sleeves, 12"',
                "Record Brushes",
            ]
        ):
            conn.execute(supply.insert().values(id=i, name=name, quantity=10))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()

        rows = conn.execute(
            sa.text(
                "SELECT name, used_for, record_size, per_units, pack_size,"
                " pack_used, min_quantity FROM supply ORDER BY id"
            )
        )
        assert [tuple(r) for r in rows] == [
            ("LP Mailers", "shipping", None, 5, 1, 0, 0),
            ('Vinyl Outer Sleeves, 7"', "shipping", '7"', 1, 1, 0, 0),
            ("Shipping Label Roll", "shipping", None, 0, 220, 0, 0),
            ("Packing Tape Rolls", "shipping", None, None, 1, 0, 0),
            ('Vinyl Inner Sleeves, 12"', "unit", None, None, 1, 0, 0),
            ("Record Brushes", None, None, None, 1, 0, 0),
        ]

        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        columns = {c["name"] for c in sa.inspect(conn).get_columns("supply")}
        assert columns == {"id", "name", "quantity"}


def test_archived_columns_start_false_on_existing_rows():
    migration = _migration("a7d3b9e14c60_add_archived_columns")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        for table in migration.TABLES:
            conn.execute(sa.text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)"))
            conn.execute(sa.text(f"INSERT INTO {table} (id) VALUES (1)"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        for table in migration.TABLES:
            assert conn.execute(sa.text(f"SELECT archived FROM {table}")).scalar() == 0
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        for table in migration.TABLES:
            columns = [c["name"] for c in sa.inspect(conn).get_columns(table)]
            assert columns == ["id"]


def test_existing_listings_start_not_ended_and_sync_record_is_created():
    migration = _migration("d9e2b6f4a173_add_ended_to_ebay_listing_and_sync_record")
    engine = sa.create_engine("sqlite://")
    listing = sa.Table(
        "ebay_listing",
        sa.MetaData(),
        sa.Column("item_id", sa.String(20), primary_key=True),
    )
    with engine.begin() as conn:
        listing.create(conn)
        conn.execute(listing.insert().values(item_id="1"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()

        row = conn.execute(sa.text("SELECT ended, end_time FROM ebay_listing")).one()
        assert tuple(row) == (0, None)
        assert "sync_record" in sa.inspect(conn).get_table_names()


def test_insertion_calibration_table_is_created():
    migration = _migration("e3a7c1d9b250_add_insertion_calibration")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert "insertion_calibration" in sa.inspect(conn).get_table_names()


def test_renews_at_is_added_empty():
    migration = _migration("f4b8d2e6a391_add_renews_at_to_ebay_listing")
    engine = sa.create_engine("sqlite://")
    listing = sa.Table(
        "ebay_listing",
        sa.MetaData(),
        sa.Column("item_id", sa.String(20), primary_key=True),
    )
    with engine.begin() as conn:
        listing.create(conn)
        conn.execute(listing.insert().values(item_id="1"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        row = conn.execute(sa.text("SELECT renews_at FROM ebay_listing")).one()
        assert row[0] is None


def test_mystery_box_columns_are_added_and_removed():
    migration = _migration("a7c3e9d15b42_add_mystery_box_to_ebay_listing")
    engine = sa.create_engine("sqlite://")
    meta = sa.MetaData()
    sa.Table("storage_box", meta, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table(
        "ebay_listing", meta, sa.Column("item_id", sa.String(20), primary_key=True)
    )
    with engine.begin() as conn:
        meta.create_all(conn)
        conn.execute(sa.text("INSERT INTO ebay_listing (item_id) VALUES ('1')"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            columns = {c["name"] for c in sa.inspect(conn).get_columns("ebay_listing")}
            assert {"pick_from_box_id", "pick_count"} <= columns
            migration.downgrade()
        columns = {c["name"] for c in sa.inspect(conn).get_columns("ebay_listing")}
        assert columns == {"item_id"}
