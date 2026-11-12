"""The scheduler: what is due, busy and failed syncs, and where it's off."""

import datetime
import logging
from collections import namedtuple

import pytest

from fbc import devmode, scheduler, sync, syncjobs

NOW = datetime.datetime(2024, 6, 1, 12, 0)
MIN = datetime.timedelta(minutes=1)


@pytest.fixture(autouse=True)
def info_logs(caplog):
    caplog.set_level(logging.INFO)


@pytest.fixture
def schedule(app):
    return scheduler.read_schedule(app.config)


@pytest.fixture
def ran(monkeypatch):
    """Replace the jobs with ones that record that they ran; the outcome of
    each is set in `outcome` (default "done")."""

    class Calls(list):
        outcome = {}

    calls = Calls()
    outcome = calls.outcome
    Job = namedtuple("Job", "kind title fn")

    def make(name):
        def build():
            def fn():
                calls.append(name)
                if outcome.get(name) == "fail":
                    raise RuntimeError("eBay is down")
                # As the real syncs do when they finish.
                sync.record_finished(name)

            return Job(name, name + " sync", fn)

        return build

    monkeypatch.setattr(scheduler, "JOBS", {n: make(n) for n in scheduler.JOBS})
    return calls


def test_everything_is_due_before_any_sync_has_finished(schedule, ran):
    outcomes = scheduler.run_due(schedule, {}, NOW)

    assert ran == ["ebay-orders", "ebay-listings", "discogs"]
    assert set(outcomes.values()) == {"done"}


def test_a_sync_is_due_once_its_interval_has_passed(schedule, ran):
    sync.record_finished("ebay-orders", NOW - 14 * MIN)
    sync.record_finished("ebay-listings", NOW - 31 * MIN)
    sync.record_finished("discogs", NOW - 119 * MIN)

    assert scheduler.due(schedule, {}, NOW) == ["ebay-listings"]


def test_intervals_come_from_the_settings(app, monkeypatch):
    monkeypatch.setitem(app.config, "SCHEDULER_DISCOGS_MINUTES", 5)
    monkeypatch.setitem(app.config, "SCHEDULER_EBAY_ORDERS_MINUTES", "nope")

    schedule = scheduler.read_schedule(app.config)

    assert schedule.intervals["discogs"] == 5 * MIN
    assert schedule.intervals["ebay-orders"] == 15 * MIN


def test_a_failed_sync_waits_for_the_retry_delay(schedule, ran):
    for name in scheduler.JOBS:
        sync.record_finished(name, NOW - 1000 * MIN)
    ran.outcome["discogs"] = "fail"
    failed_at = {}

    outcomes = scheduler.run_due(schedule, failed_at, NOW)

    assert outcomes["discogs"] == "failed"
    assert "discogs" in failed_at
    # Failed a minute ago: not retried yet. Ten minutes on, it is.
    assert scheduler.due(schedule, failed_at, sync.utcnow() + 5 * MIN) == []
    assert scheduler.due(schedule, failed_at, sync.utcnow() + 11 * MIN) == ["discogs"]


def test_a_busy_sync_is_skipped_and_not_counted_as_failed(schedule, ran, monkeypatch):
    def busy(*args, **kwargs):
        raise sync.Busy("already running")

    monkeypatch.setattr(sync, "run", busy)
    failed_at = {}

    outcomes = scheduler.run_due(schedule, failed_at, NOW)

    assert set(outcomes.values()) == {"busy"}
    assert failed_at == {}


def test_a_sync_that_finished_is_not_run_again_on_the_next_tick(schedule, ran):
    scheduler.run_due(schedule, {}, sync.utcnow())
    ran.clear()

    scheduler.run_due(schedule, {}, sync.utcnow() + MIN)

    assert ran == []


def test_the_loop_runs_what_is_due_then_sleeps(app, ran):
    with pytest.raises(KeyboardInterrupt):
        scheduler.loop(
            app.config, sleep=lambda s: (_ for _ in ()).throw(KeyboardInterrupt)
        )

    assert ran == ["ebay-orders", "ebay-listings", "discogs"]


def test_the_loop_survives_a_failing_tick(app, monkeypatch):
    sleeps = []

    def broken(*args):
        raise RuntimeError("database gone")

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(scheduler, "run_due", broken)

    with pytest.raises(KeyboardInterrupt):
        scheduler.loop(app.config, sleep=sleep)

    assert sleeps == [scheduler.TICK_SECONDS] * 2


def test_it_does_nothing_in_dev_mode(app, monkeypatch, caplog):
    monkeypatch.setitem(app.config, "DEV_MODE", True)
    called = []
    monkeypatch.setattr(scheduler, "run_due", lambda *a: called.append(a))

    scheduler.loop(app.config, sleep=lambda s: called.append("slept"))

    assert called == []
    assert devmode.message("scheduled syncs") in caplog.text


def test_it_does_nothing_when_turned_off(app, monkeypatch, caplog):
    monkeypatch.setitem(app.config, "SCHEDULER", False)
    called = []
    monkeypatch.setattr(scheduler, "run_due", lambda *a: called.append(a))

    scheduler.loop(app.config, sleep=lambda s: called.append("slept"))

    assert called == []
    assert "scheduled syncs are off" in caplog.text


def test_the_command_exits_at_once_in_dev_mode(app, monkeypatch):
    monkeypatch.setitem(app.config, "DEV_MODE", True)

    result = app.test_cli_runner().invoke(args=["scheduler"])

    assert result.exit_code == 0


def test_the_real_jobs_are_the_ones_the_buttons_run():
    assert scheduler.JOBS["discogs"] is syncjobs.discogs_job
    assert scheduler.JOBS["ebay-listings"] is syncjobs.ebay_listings_job


def test_a_sync_that_logs_an_error_and_records_nothing_waits_to_retry(
    schedule, monkeypatch
):
    # The eBay syncs do this when eBay isn't connected: log, return, record
    # nothing. It must not be retried on every tick.
    import logging
    from collections import namedtuple

    Job = namedtuple("Job", "kind title fn")

    def fn():
        logging.getLogger().error("eBay isn't connected")

    monkeypatch.setattr(
        scheduler, "JOBS", {"ebay-orders": lambda: Job("ebay-orders", "Orders", fn)}
    )
    failed_at = {}

    outcomes = scheduler.run_due(schedule, failed_at, NOW)

    assert outcomes == {"ebay-orders": "failed"}
    assert scheduler.due(schedule, failed_at, sync.utcnow() + MIN) == []
