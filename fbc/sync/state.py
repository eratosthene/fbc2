"""
Running the Discogs and eBay syncs in the background. A sync can take longer
than the web server's timeout, so the Sync buttons start it in a thread and
return at once, and the page then polls for the sync's log until it's done.

A job's state and log are kept in files under SYNC_JOB_FOLDER (a folder in the
system temp folder by default), not in memory: under gunicorn the request that
polls a job may reach a different worker process from the one running it.

The `flask sync` commands run the same jobs in the foreground (see `run`),
through the same files, so a command and a button, or two commands, never run
the same sync at once, even from different processes in one container.
"""

import contextlib
import datetime
import fcntl
import json
import logging
import os
import re
import tempfile
import threading
import time

logger = logging.getLogger()

# The sync_record name of the eBay listings sync.
LISTINGS_SYNC = "ebay-listings"

# Job files older than this are deleted when the next job starts.
KEEP_SECONDS = 24 * 60 * 60

_JOB_ID = re.compile(r"[0-9a-f]{32}")


def _folder():
    from fbc import app

    folder = app.config.get("SYNC_JOB_FOLDER") or os.path.join(
        tempfile.gettempdir(), "fbc-sync-jobs"
    )
    os.makedirs(folder, exist_ok=True)
    return folder


@contextlib.contextmanager
def _claiming(folder):
    """
    Hold a lock while checking for a running job and writing a new one's state,
    so two processes can't both see none running and both start.
    """
    with open(os.path.join(folder, "lock"), "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def _state_path(folder, job_id):
    return os.path.join(folder, job_id + ".json")


def _log_path(folder, job_id):
    return os.path.join(folder, job_id + ".log")


def _read_state(folder, job_id):
    try:
        with open(_state_path(folder, job_id), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_state(folder, state):
    # Write a new file and move it into place, so a poll never reads half of it.
    path = _state_path(folder, state["id"])
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(path + ".tmp", path)


def _append_log(folder, job_id, line):
    with open(_log_path(folder, job_id), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _check_alive(folder, state):
    """
    Mark a running job failed if the process running it has gone (the server
    restarted, or gunicorn replaced the worker), since it will never finish.
    """
    if state["status"] == "running" and not _process_alive(state["pid"]):
        _append_log(
            folder,
            state["id"],
            "The server stopped before the sync finished. Run it again.",
        )
        state = dict(state, status="failed", finished=_now())
        _write_state(folder, state)
    return state


def _running_job(folder, kind):
    """
    The id of the running job of this kind, if there is one. Also deletes the
    files of jobs that finished more than KEEP_SECONDS ago.
    """
    found = None
    cutoff = time.time() - KEEP_SECONDS
    for name in os.listdir(folder):
        job_id, ext = os.path.splitext(name)
        if ext != ".json" or not _JOB_ID.fullmatch(job_id):
            continue
        state = _read_state(folder, job_id)
        if state is None:
            continue
        state = _check_alive(folder, state)
        if state["status"] == "running":
            if state["kind"] == kind:
                found = job_id
        elif os.path.getmtime(_state_path(folder, job_id)) < cutoff:
            for path in (_state_path(folder, job_id), _log_path(folder, job_id)):
                try:
                    os.remove(path)
                except OSError:
                    pass
    return found


class _JobLogHandler(logging.Handler):
    """Copies the log records of one thread to a job's log file."""

    def __init__(self, folder, job_id, thread_id, echo=None):
        super().__init__(logging.INFO)
        self.echo = echo
        self.folder = folder
        self.job_id = job_id
        self.thread_id = thread_id
        self.errors = 0
        self.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
        )

    def emit(self, record):
        if record.thread != self.thread_id:
            return
        if record.levelno >= logging.ERROR:
            self.errors += 1
        try:
            line = self.format(record)
            _append_log(self.folder, self.job_id, line)
            if self.echo:
                self.echo(line)
        except Exception:
            self.handleError(record)


def _spawn(target):
    threading.Thread(target=target, name="fbc-sync", daemon=True).start()


def _claim(kind, title, return_url):
    """
    Write the state of a new running job of this kind and return (state, None),
    or (None, id) of the one already running.
    """
    import uuid

    folder = _folder()
    with _claiming(folder):
        running = _running_job(folder, kind)
        if running:
            return None, running
        job_id = uuid.uuid4().hex
        state = {
            "id": job_id,
            "kind": kind,
            "title": title,
            "return_url": return_url,
            "pid": os.getpid(),
            "status": "running",
            "started": _now(),
            "finished": None,
            "errors": 0,
        }
        open(_log_path(folder, job_id), "w").close()
        _write_state(folder, state)
    return state, None


def _execute(state, fn, echo=None):
    """
    Run fn() in an app context in this thread, logging to the job's log (and
    to echo, if given), and write the job's final state. Returns it.
    """
    from fbc import app

    folder = _folder()
    root = logging.getLogger()
    handler = _JobLogHandler(folder, state["id"], threading.get_ident(), echo)
    root.addHandler(handler)
    status = "failed"
    try:
        with app.app_context():
            fn()
        status = "done"
    except Exception:
        logger.exception(state["title"] + " failed")
    finally:
        root.removeHandler(handler)
        state = dict(state, status=status, finished=_now(), errors=handler.errors)
        _write_state(folder, state)
    return state


def start(kind, title, fn, return_url):
    """
    Run fn() in the background, in an app context, and return the job's id.
    Everything fn logs at INFO and above goes to the job's log. If a job of
    the same kind is still running, return that one's id instead of starting
    another, so two syncs don't add the same things twice.
    """
    state, running = _claim(kind, title, return_url)
    if running:
        return running
    _spawn(lambda: _execute(state, fn))
    return state["id"]


class Busy(Exception):
    """A job of the kind is already running, in this or another process."""


def run(kind, title, fn, echo=print):
    """
    Run fn() now, in this thread, as `start` would in the background: the same
    job files (so the Sync button's modal can show it) and the same refusal,
    as Busy, if a job of the kind is already running. Each log line is also
    passed to echo. Returns the job's final state; its status is "done" or
    "failed", and its errors count the errors it logged and carried on from.
    """
    state, running = _claim(kind, title, "/")
    if running:
        raise Busy(title + " is already running.")
    return _execute(state, fn, echo)


def read(job_id, offset=0):
    """
    A job's state, with the part of its log from byte offset on and the offset
    to ask for next, or None if there's no such job.
    """
    if not _JOB_ID.fullmatch(job_id or ""):
        return None
    folder = _folder()
    state = _read_state(folder, job_id)
    if state is None:
        return None
    # Read the state before the log: once a job's state says it's finished,
    # everything it logged is already in the file.
    state = _check_alive(folder, state)
    try:
        with open(_log_path(folder, job_id), "rb") as f:
            f.seek(max(offset, 0))
            data = f.read()
    except OSError:
        data = b""
    if state["status"] == "running":
        # Leave a line that's still being written for the next poll.
        data = data[: data.rfind(b"\n") + 1]
    return {
        "id": job_id,
        "title": state["title"],
        "status": state["status"],
        # How many errors a finished job logged. The syncs log some (eBay
        # not connected, a page that didn't come back) and carry on.
        "errors": state.get("errors", 0),
        "return_url": state["return_url"],
        "log": data.decode("utf-8", "replace"),
        "offset": max(offset, 0) + len(data),
    }


def record_finished(name, when=None):
    """Remember that the sync called `name` just finished successfully."""
    from fbc import db
    from fbc.models.sync import SyncRecord

    when = when or utcnow()
    record = db.session.get(SyncRecord, name)
    if record is None:
        db.session.add(SyncRecord(name=name, finished=when))
    else:
        record.finished = when
    db.session.commit()


def last_finished(name):
    """The UTC time the sync called `name` last finished, or None."""
    from fbc import db
    from fbc.models.sync import SyncRecord

    record = db.session.get(SyncRecord, name)
    return record.finished if record else None


def utcnow():
    """Now as a naive UTC datetime, which is how fbc stores eBay's times."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
