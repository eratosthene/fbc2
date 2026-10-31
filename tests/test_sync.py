import json
import logging
import os
import threading
import time

import pytest

from fbc import sync
from fbc.discogs import DiscogsAuthError


@pytest.fixture
def info_logs(caplog):
    # conftest turns the root logger down to ERROR; jobs log at INFO.
    caplog.set_level(logging.INFO)


@pytest.fixture
def blocked():
    """A job body that waits until the test is over."""
    go_on = threading.Event()
    yield lambda: go_on.wait(5)
    go_on.set()


def _wait_until_finished(job_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = sync.read(job_id)
        if job["status"] != "running":
            return job
        time.sleep(0.01)
    raise AssertionError("The job didn't finish.")


def test_a_job_logs_and_finishes(inline_sync, info_logs):
    def fn():
        logging.getLogger().info("first")
        logging.getLogger("fbc.something").warning("second")
        logging.getLogger().debug("left out")

    job_id = sync.start("test", "Test sync", fn, "/back/")
    job = sync.read(job_id)

    assert job["status"] == "done"
    assert job["title"] == "Test sync"
    assert job["return_url"] == "/back/"
    lines = job["log"].splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("INFO first")
    assert lines[1].endswith("WARNING second")
    assert job["errors"] == 0


def test_a_job_counts_the_errors_it_logged_and_carried_on_from(inline_sync):
    def fn():
        logging.getLogger().error("page 1 didn't come back")
        logging.getLogger().error("page 2 didn't come back")

    job = sync.read(sync.start("test", "Test sync", fn, "/back/"))

    assert job["status"] == "done"
    assert job["errors"] == 2


def test_reading_from_an_offset_returns_only_the_rest(inline_sync, info_logs):
    def fn():
        logging.getLogger().info("first")
        logging.getLogger().info("second")

    job_id = sync.start("test", "Test sync", fn, "/back/")
    first = sync.read(job_id)
    rest = sync.read(job_id, first["offset"])

    assert rest["log"] == ""
    assert rest["offset"] == first["offset"]
    line_one = first["log"].splitlines(keepends=True)[0]
    assert sync.read(job_id, len(line_one.encode()))["log"].endswith("second\n")


def test_a_job_that_raises_is_failed_with_its_traceback(inline_sync):
    def fn():
        raise DiscogsAuthError("Connect fbc to Discogs first.")

    job = sync.read(sync.start("test", "Test sync", fn, "/back/"))

    assert job["status"] == "failed"
    assert "Test sync failed" in job["log"]
    assert "DiscogsAuthError: Connect fbc to Discogs first." in job["log"]


def test_a_job_runs_in_its_own_thread_and_logs_only_its_own_records(info_logs):
    started = threading.Event()
    go_on = threading.Event()

    def fn():
        started.set()
        go_on.wait(5)
        logging.getLogger().info("from the job")

    job_id = sync.start("test", "Test sync", fn, "/back/")
    assert started.wait(5)
    assert sync.read(job_id)["status"] == "running"
    logging.getLogger().info("from the request")
    go_on.set()
    job = _wait_until_finished(job_id)

    assert job["status"] == "done"
    assert "from the job" in job["log"]
    assert "from the request" not in job["log"]


def test_a_running_job_hides_a_line_still_being_written(sync_job_folder, blocked):
    job_id = sync.start("test", "Test sync", blocked, "/back/")
    with open(sync_job_folder / (job_id + ".log"), "a") as f:
        f.write("whole line\nhalf a li")

    job = sync.read(job_id)

    assert job["status"] == "running"
    assert job["log"] == "whole line\n"
    assert sync.read(job_id, job["offset"])["log"] == ""


def test_starting_a_kind_that_is_running_returns_the_running_job(blocked):
    first = sync.start("discogs", "Discogs sync", blocked, "/back/")
    again = sync.start("discogs", "Discogs sync", blocked, "/back/")
    other = sync.start("ebay-orders", "eBay orders sync", blocked, "/back/")

    assert again == first
    assert other != first


def test_a_job_whose_process_has_gone_is_failed(monkeypatch, blocked):
    job_id = sync.start("test", "Test sync", blocked, "/back/")
    monkeypatch.setattr(sync, "_process_alive", lambda pid: False)

    job = sync.read(job_id)

    assert job["status"] == "failed"
    assert "The server stopped before the sync finished." in job["log"]
    # And a new sync of that kind can start.
    assert sync.start("test", "Test sync", lambda: None, "/back/") != job_id


def test_finished_jobs_are_deleted_after_a_day(inline_sync, sync_job_folder):
    old = sync.start("test", "Test sync", lambda: None, "/back/")
    a_while_ago = time.time() - sync.KEEP_SECONDS - 60
    os.utime(sync_job_folder / (old + ".json"), (a_while_ago, a_while_ago))
    recent = sync.start("test", "Test sync", lambda: None, "/back/")

    sync.start("test", "Test sync", lambda: None, "/back/")

    assert sync.read(old) is None
    assert not (sync_job_folder / (old + ".log")).exists()
    assert sync.read(recent)["status"] == "done"


@pytest.mark.parametrize("job_id", ["", "nope", "../../etc/passwd", "A" * 32])
def test_reading_something_that_isnt_a_job_id_returns_none(job_id):
    assert sync.read(job_id) is None


def test_the_job_state_is_json_on_disk(inline_sync, sync_job_folder):
    job_id = sync.start("test", "Test sync", lambda: None, "/back/")

    with open(sync_job_folder / (job_id + ".json")) as f:
        state = json.load(f)

    assert state["kind"] == "test"
    assert state["status"] == "done"
    assert state["pid"] == os.getpid()


def test_sync_link_from_the_page_script_gets_the_job_as_json(
    app, monkeypatch, inline_sync
):
    from fbc import index

    def no_discogs():
        raise DiscogsAuthError("Connect fbc to Discogs first.")

    monkeypatch.setattr(index.discogs, "client", no_discogs)
    client = app.test_client()

    response = client.get(
        "/syncdiscogs",
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "http://localhost/discogsreleasemodelview/list/?page=2",
        },
    )

    assert response.status_code == 200
    started = response.get_json()
    assert started["url"] == "/syncjob/" + started["id"]
    job = client.get(started["url"]).get_json()
    assert job["status"] == "failed"
    assert "Connect fbc to Discogs first." in job["log"]
    assert job["return_url"] == "http://localhost/discogsreleasemodelview/list/?page=2"


def test_sync_link_opened_as_a_page_shows_the_job(app, monkeypatch, inline_sync):
    from fbc import index

    monkeypatch.setattr(index.MyIndexView, "dosyncdiscogs", lambda self: None)
    client = app.test_client()

    response = client.get("/syncdiscogs", headers={"Accept": "text/html"})

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'id="fbc-sync-modal"' in page
    assert "fbcSync.watch(" in page
    job_id = page.split('fbcSync.watch("/syncjob/')[1][:32]
    job = client.get("/syncjob/" + job_id).get_json()
    assert job["status"] == "done"
    # With no referrer, Close goes back to the Releases list.
    assert job["return_url"] == "/discogsreleasemodelview/list/"


def test_unknown_job_is_a_404(app):
    response = app.test_client().get("/syncjob/" + "0" * 32)

    assert response.status_code == 404


@pytest.mark.parametrize(
    "path,widget",
    [
        ("/discogsreleasemodelview/list/", "/syncdiscogs"),
        ("/ebaylistingmodelview/list/", "/syncebaylistings"),
        ("/ebayordermodelview/list/", "/syncebayordersdeep"),
    ],
)
def test_list_pages_open_syncs_in_the_modal(app, path, widget):
    from fbc import appbuilder

    sm = appbuilder.sm
    sm.create_db()
    appbuilder.add_permissions(update_perms=True)
    sm.add_user("admin", "A", "Dmin", "admin@example.com", sm.find_role("Admin"), "pw")
    client = app.test_client()
    client.post("/login/", data={"username": "admin", "password": "pw"})

    page = client.get(path).get_data(as_text=True)

    assert 'href="' + widget + '" class="btn btn-sm btn-primary fbc-sync"' in page
    assert 'id="fbc-sync-modal"' in page
