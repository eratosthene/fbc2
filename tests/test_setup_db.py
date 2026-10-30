import pytest
import sqlalchemy as sa
from alembic.script import ScriptDirectory

import fbc


@pytest.fixture
def run_setup_db(app):
    def run():
        result = app.test_cli_runner().invoke(args=["setup-db"])
        assert result.exception is None, result.output
        return result

    return run


@pytest.fixture(autouse=True)
def drop_alembic_version(db):
    # alembic_version isn't one of our models, so the shared fixtures' drop_all()
    # would leave it behind for the next test.
    yield
    with db.engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))


def _tables(db):
    return set(sa.inspect(db.engine).get_table_names())


def test_empty_database_is_built_from_the_models_and_stamped_at_head(
    db, run_setup_db, mocker
):
    upgrade = mocker.patch("fbc.upgrade")
    db.drop_all()

    result = run_setup_db()

    assert set(db.metadata.tables) <= _tables(db)
    head = ScriptDirectory.from_config(fbc.migrate.get_config()).get_current_head()
    with db.engine.connect() as conn:
        versions = conn.execute(sa.text("SELECT version_num FROM alembic_version"))
        assert versions.scalars().all() == [head]
    upgrade.assert_not_called()
    assert "Created a new database" in result.output


def test_migrated_database_is_upgraded(db, run_setup_db, mocker):
    upgrade = mocker.patch("fbc.upgrade")
    stamp = mocker.patch("fbc.stamp")
    with db.engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))

    run_setup_db()

    upgrade.assert_called_once_with()
    stamp.assert_not_called()


def test_database_from_before_migrations_is_upgraded(db, run_setup_db, mocker):
    # It has the app's tables (the shared fixture creates them) but has never
    # been stamped, so it needs every migration rather than a stamp.
    upgrade = mocker.patch("fbc.upgrade")
    stamp = mocker.patch("fbc.stamp")
    create_all = mocker.patch.object(db, "create_all")
    assert "alembic_version" not in _tables(db)

    run_setup_db()

    upgrade.assert_called_once_with()
    stamp.assert_not_called()
    create_all.assert_not_called()
