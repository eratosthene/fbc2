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
