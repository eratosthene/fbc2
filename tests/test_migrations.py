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
