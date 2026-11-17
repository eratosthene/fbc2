import datetime

from fbc.models.sync import SyncRecord


def test_last_finished_is_none_before_a_record_exists():
    assert SyncRecord.last_finished("nothing-yet") is None


def test_last_finished_is_the_time_record_finished_saved():
    when = datetime.datetime(2026, 11, 1, 12, 30)
    SyncRecord.record_finished("a-sync", when)
    assert SyncRecord.last_finished("a-sync") == when


def test_record_finished_replaces_an_earlier_time():
    SyncRecord.record_finished("b-sync", datetime.datetime(2026, 11, 1, 12, 30))
    later = datetime.datetime(2026, 11, 2, 8, 0)
    SyncRecord.record_finished("b-sync", later)
    assert SyncRecord.last_finished("b-sync") == later


def test_the_listings_sync_name_is_the_same_in_jobs_and_the_model():
    from fbc.sync import jobs

    assert jobs.LISTINGS_SYNC == SyncRecord.LISTINGS_SYNC


def test_importing_fbc_loads_no_model_before_appbuilder_starts():
    # AppBuilder creates the tables of every model loaded by then, which
    # breaks the migration that makes sync_record on a database that has none.
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(
        """
        import sys, types
        from flask_appbuilder import AppBuilder, Model
        from flask_appbuilder.security.manager import AUTH_DB
        c = types.ModuleType("config")
        c.SECRET_KEY = "x"
        c.SQLALCHEMY_DATABASE_URI = "sqlite://"
        c.AUTH_TYPE = AUTH_DB
        c.EBAY_SETTINGS = {}
        c.DISCOGS_SETTINGS = {}
        sys.modules["config"] = c
        seen = []
        original = AppBuilder.__init__
        def init(self, *a, **k):
            seen.extend(Model.metadata.tables)
            original(self, *a, **k)
        AppBuilder.__init__ = init
        import fbc
        print("TABLES:", sorted(seen))
        """
    )
    root = __import__("os").path.dirname(__import__("os").path.dirname(__file__))
    out = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": root, "PATH": __import__("os").environ["PATH"]},
    )
    assert "TABLES: []" in out.stdout, out.stdout + out.stderr
