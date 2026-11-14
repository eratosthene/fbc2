"""The `flask sync` commands, which run the same jobs as the Sync buttons."""

import logging
from types import SimpleNamespace

import pytest
from fake_discogs import FakeAccount, FakeDiscogs

from fbc import sync, syncjobs
from fbc.discogs import DiscogsAuthError
from fbc.listing import OAuthError


@pytest.fixture(autouse=True)
def info_logs(caplog):
    # conftest turns the root logger down to ERROR; the app runs it at DEBUG.
    caplog.set_level(logging.INFO)


@pytest.fixture
def run(app):
    def invoke(*args):
        return app.test_cli_runner().invoke(args=["sync", *args])

    return invoke


@pytest.fixture
def dev_mode(app, monkeypatch):
    monkeypatch.setitem(app.config, "DEV_MODE", True)


def _empty_discogs(monkeypatch):
    account = FakeAccount(folders=[{"id": 0, "name": "All"}])
    FakeDiscogs(account=account).install(monkeypatch)


def test_discogs_prints_its_log_and_records_that_it_finished(run, monkeypatch):
    _empty_discogs(monkeypatch)

    result = run("discogs")

    assert result.exit_code == 0
    assert "INFO Updating discogs..." in result.output
    assert "INFO Discogs sync finished." in result.output
    assert sync.last_finished(syncjobs.DISCOGS_SYNC) is not None


def test_a_failed_sync_exits_non_zero_and_records_nothing(run, monkeypatch):
    def no_discogs():
        raise DiscogsAuthError("Connect fbc to Discogs first.")

    monkeypatch.setattr(syncjobs.discogs, "account", no_discogs)

    result = run("discogs")

    assert result.exit_code == 1
    assert "Connect fbc to Discogs first." in result.output
    assert "Discogs sync failed." in result.output
    assert sync.last_finished(syncjobs.DISCOGS_SYNC) is None


def test_ebay_listings_records_that_it_finished(run, monkeypatch):
    monkeypatch.setattr(syncjobs, "fetch_active_items", lambda: [])

    result = run("ebay-listings")

    assert result.exit_code == 0
    assert "eBay listings sync finished." in result.output
    assert sync.last_finished(sync.LISTINGS_SYNC) is not None


class _Api:
    def __init__(self, orders=()):
        self.orders = list(orders)
        self.sent = None

    def execute(self, verb, data):
        self.sent = (verb, data)
        return SimpleNamespace(dict=lambda: {"OrderArray": {"Order": self.orders}})

    def response_status(self):
        return "Success"


def test_ebay_orders_looks_back_ten_days_unless_deep(run, monkeypatch):
    api = _Api()
    monkeypatch.setattr(syncjobs, "trading", lambda compatibility=None: api)

    assert run("ebay-orders").exit_code == 0
    shown = api.sent[1]
    assert shown["CreateTimeFrom"] < shown["CreateTimeTo"]
    ten = (shown["CreateTimeFrom"], shown["CreateTimeTo"])

    result = run("ebay-orders", "--deep")

    assert result.exit_code == 0
    assert "Deep updating eBay orders..." in result.output
    assert api.sent[1]["CreateTimeFrom"] < ten[0]
    assert sync.last_finished(syncjobs.ORDERS_SYNC) is not None


def test_ebay_orders_not_connected_is_a_failure(run, monkeypatch):
    def not_connected(compatibility=None):
        raise OAuthError("Connect fbc to eBay first.")

    monkeypatch.setattr(syncjobs, "trading", not_connected)

    result = run("ebay-orders")

    # The sync logs the error and returns, which counts as a failure here.
    assert result.exit_code == 1
    assert "Connect fbc to eBay first." in result.output
    assert sync.last_finished(syncjobs.ORDERS_SYNC) is None


def test_a_sync_that_logs_an_error_and_carries_on_exits_non_zero(run, monkeypatch):
    def carries_on():
        logging.getLogger().error("page 2 didn't come back")

    monkeypatch.setattr(syncjobs, "sync_discogs", carries_on)

    result = run("discogs")

    assert result.exit_code == 1
    assert "logged 1 errors" in result.output


def test_a_command_refuses_while_the_same_sync_is_running(run, monkeypatch):
    called = []
    monkeypatch.setattr(syncjobs, "sync_discogs", lambda: called.append(1))
    # What a button, or a command in another process, leaves while it runs.
    state, _ = sync._claim("discogs", "Discogs sync", "/")

    result = run("discogs")

    assert result.exit_code == 1
    assert "Discogs sync is already running." in result.output
    assert called == []
    # The orders sync is a different kind, so it isn't held up.
    monkeypatch.setattr(syncjobs, "trading", lambda compatibility=None: _Api())
    assert run("ebay-orders").exit_code == 0


def test_a_command_and_a_button_share_the_one_job_check(run, monkeypatch, inline_sync):
    seen = []

    def during():
        # A button pressed while the command runs gets the command's job.
        seen.append(sync.start("discogs", "Discogs sync", lambda: None, "/"))
        seen.append(sync._running_job(sync._folder(), "discogs"))

    monkeypatch.setattr(syncjobs, "sync_discogs", during)

    assert run("discogs").exit_code == 0
    assert seen[0] == seen[1]
    # Once it finished, a new one can start.
    assert sync._running_job(sync._folder(), "discogs") is None


def test_the_command_shows_up_as_a_job_the_buttons_modal_can_read(run, monkeypatch):
    _empty_discogs(monkeypatch)
    run("discogs")

    jobs = [n for n in __import__("os").listdir(sync._folder()) if n.endswith(".json")]
    job = sync.read(jobs[0][:-5])
    assert job["status"] == "done"
    assert "Discogs sync finished." in job["log"]


@pytest.mark.parametrize(
    "args",
    [("discogs",), ("ebay-listings",), ("ebay-orders",), ("ebay-orders", "--deep")],
)
def test_in_dev_mode_a_command_finishes_without_reaching_out(
    run, dev_mode, monkeypatch, args
):
    def fails(*a, **k):
        raise AssertionError("reached out")

    for name in ("sync_discogs", "sync_ebay_listings", "sync_ebay_orders"):
        monkeypatch.setattr(syncjobs, name, fails)

    result = run(*args)

    assert result.exit_code == 0
    assert "Dev Mode is turned on, external syncing disabled." in result.output
    assert sync.last_finished(syncjobs.DISCOGS_SYNC) is None
