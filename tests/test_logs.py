import logging
import subprocess
import sys
from pathlib import Path

import pytest

from fbc.core import logs

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "value, level",
    [
        ("debug", logging.DEBUG),
        ("DEBUG", logging.DEBUG),
        ("Info", logging.INFO),
        ("warning", logging.WARNING),
        (" error ", logging.ERROR),
        ("critical", logging.CRITICAL),
        ("loud", logging.INFO),
        ("", logging.INFO),
        ("notset", logging.INFO),
        ("10", logging.INFO),
        (None, logging.INFO),
    ],
)
def test_parse_level(value, level):
    assert logs.parse_level(value) == level


@pytest.fixture
def restore_loggers():
    root, sdk = logging.getLogger(), logging.getLogger(logs.SDK_LOGGER)
    saved = root.level, sdk.level
    yield
    root.setLevel(saved[0])
    sdk.setLevel(saved[1])


def test_unset_is_info_and_the_sdk_is_quiet(restore_loggers):
    assert logs.init_logging({}) == logging.INFO
    assert logging.getLogger().level == logging.INFO
    assert logging.getLogger(logs.SDK_LOGGER).level == logging.WARNING


def test_debug_lets_the_sdk_log_debug(restore_loggers):
    logs.init_logging({"LOG_LEVEL": "debug"})
    assert logging.getLogger().level == logging.DEBUG
    assert logging.getLogger(logs.SDK_LOGGER).level == logging.DEBUG


def test_error_keeps_the_sdk_at_warning_or_above(restore_loggers):
    logs.init_logging({"LOG_LEVEL": "error"})
    assert logging.getLogger(logs.SDK_LOGGER).level == logging.WARNING


def test_importing_the_app_does_not_force_debug():
    code = (
        "import logging, sys, types\n"
        "sys.path.insert(0, 'tests')\n"
        "import conftest\n"
        "conftest._install_fake_config()\n"
        "import fbc\n"
        "print(logging.getLogger().level, logging.getLogger('ebaysdk').level)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={"LOG_LEVEL": "warning", "PATH": "/usr/bin"},
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split()[-2:] == [str(logging.WARNING)] * 2
