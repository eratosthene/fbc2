import ast
import datetime
import importlib.util
import io
import os
import shutil
import stat
import sys

import pytest

_scripts = os.path.join(os.path.dirname(__file__), "..", "scripts")
_spec = importlib.util.spec_from_file_location(
    "move_secrets_to_env", os.path.join(_scripts, "move_secrets_to_env.py")
)
move = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(move)

NOW = datetime.datetime(2026, 11, 1, 12, 0, 0)

TEMPLATE = """\
import os
from not_installed.security.manager import AUTH_DB

basedir = os.path.abspath(os.path.dirname(__file__))

# Your App secret key
SECRET_KEY = "1234567890"

SQLALCHEMY_DATABASE_URI = os.environ.get(
    "SQLALCHEMY_DATABASE_URI", 'mysql+pymysql://user:password@host/db'
)

AUTH_TYPE = AUTH_DB
DEV_MODE = False
APP_THEME = "cyborg.css"  # not too bad

EBAY_SETTINGS = {
    'APP_ID': '',
    'CERT_ID': '',
    'LISTING': {
        'CATEGORY_ID': '176985',
        'BEST_OFFER': True,
        'PACKAGE': {
            'WEIGHT_OZ': {'12"': 16, '7"': 8},
        },
    },
}
"""

LIVE = """\
import os
import datetime
from not_installed.security.manager import AUTH_DB

basedir = os.path.abspath(os.path.dirname(__file__))
db_host = "192.0.2.10"

SECRET_KEY = "s3cr3t-$key#1"   # my note
SQLALCHEMY_DATABASE_URI = "mysql+pymysql://fbc:pw@" + db_host + "/fbc"
AUTH_TYPE = AUTH_DB
APP_THEME = "slate.css"
APP_THEME = "darkly.css"

EBAY_SETTINGS = {
        'APP_ID': 'Shop-fbc-PRD-1234',
        'CERT_ID': '4200',
        'LISTING': {
            'CATEGORY_ID': '176985',
            'POSTAL_CODE': '01234',
            'PACKAGE': {'WEIGHT_OZ': {'12"': 18, '7"': 8}},
        },
        'EXTRA': [
            1,
            2,
        ],
}

MAIL_PASSWORD = "hunter2"
PERMANENT_SESSION_LIFETIME = datetime.timedelta(days=7)
EBAY_SETTINGS["LISTING"]["LOCATION"] = "Here"
"""


def _repo(tmp_path, live=LIVE, template=TEMPLATE, env=None):
    (tmp_path / "config.py").write_text(live)
    (tmp_path / "config.py.tpl").write_text(template)
    (tmp_path / "docker").mkdir()
    if env is not None:
        (tmp_path / "docker" / ".env").write_text(env)
    return tmp_path


def _settings(repo):
    env_path = repo / "docker" / ".env"
    env = move.dump_for_dev.env_file(str(env_path)) if env_path.exists() else {}
    return move.effective(
        (repo / "config.py").read_text(), str(repo / "config.py"), env
    )[1]


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    monkeypatch.delenv("SQLALCHEMY_DATABASE_URI", raising=False)
    yield
    sys.modules.pop("not_installed", None)


def test_moves_secrets_and_reconciles(tmp_path):
    repo = _repo(tmp_path)
    before = _settings(repo)
    plan = move.Plan(str(repo))

    assert plan.changed == []
    assert plan.moved == {
        ("SECRET_KEY",): "FBC_CONFIG_SECRET_KEY='s3cr3t-$key#1'",
        (
            "EBAY_SETTINGS",
            "APP_ID",
        ): "FBC_CONFIG_EBAY_SETTINGS__APP_ID='Shop-fbc-PRD-1234'",
        # Would read as the number 4200, so it's written as a JSON string.
        ("EBAY_SETTINGS", "CERT_ID"): "FBC_CONFIG_EBAY_SETTINGS__CERT_ID='\"4200\"'",
    }
    # Built from a variable, so there's no one string to blank.
    assert plan.left == [
        (("SQLALCHEMY_DATABASE_URI",), "its value isn't a plain string there")
    ]
    rebuild = plan.rebuild
    assert rebuild.only_template == [
        ("DEV_MODE",),
        ("EBAY_SETTINGS", "LISTING", "BEST_OFFER"),
    ]
    assert rebuild.only_live == [
        ("EBAY_SETTINGS", "LISTING", "POSTAL_CODE"),
        ("EBAY_SETTINGS", "EXTRA"),
        ("MAIL_PASSWORD",),
        ("PERMANENT_SESSION_LIFETIME",),
    ]
    assert rebuild.kept_statements == 1

    out = io.StringIO()
    plan.report(out)
    report = out.getvalue()
    assert 'APP_THEME: "cyborg.css" -> "darkly.css"' in report
    assert "MAIL_PASSWORD" in report.split("Might be secrets")[1]
    assert "the app sees the same settings" in report
    assert "s3cr3t" not in report and "Shop-fbc" not in report

    out = io.StringIO()
    plan.diff(out)
    assert "s3cr3t" not in out.getvalue() and "<secret>" in out.getvalue()

    backup = plan.apply(NOW)

    new = (repo / "config.py").read_text()
    assert "s3cr3t" not in new and "Shop-fbc" not in new and "4200" not in new
    assert '# Your App secret key\nSECRET_KEY = ""\n' in new
    assert "# Not set in the old config.py:\n# DEV_MODE = False\n" in new
    assert "        # 'BEST_OFFER': True,\n" in new
    assert "        'POSTAL_CODE': '01234',\n" in new
    assert "    'EXTRA': [\n        1,\n        2,\n    ],\n" in new
    assert 'db_host = "192.0.2.10"' in new
    assert "import datetime" in new
    ast.parse(new)
    assert _settings(repo) == before

    env = (repo / "docker" / ".env").read_text()
    assert env.startswith("# Moved from config.py by scripts/move_secrets_to_env.py,")
    assert "FBC_CONFIG_SECRET_KEY='s3cr3t-$key#1'" in env
    for path in (repo / "config.py", repo / "docker" / ".env", backup):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert backup.endswith("config.py.bak-20261101-120000")
    with open(backup) as f:
        assert f.read() == LIVE

    # A second run has nothing left to do.
    assert move.Plan(str(repo)).nothing_to_do()


def test_the_template_itself(tmp_path):
    """The real config.py.tpl, used as config.py: no drift, and its
    placeholder secrets move."""
    template = os.path.join(os.path.dirname(__file__), "..", "config.py.tpl")
    shutil.copy(template, tmp_path / "config.py")
    shutil.copy(template, tmp_path / "config.py.tpl")
    (tmp_path / "docker").mkdir()
    before = _settings(tmp_path)
    plan = move.Plan(str(tmp_path))

    assert plan.changed == []
    assert sorted(plan.moved) == [("SECRET_KEY",), ("SQLALCHEMY_DATABASE_URI",)]
    assert plan.moved[("SECRET_KEY",)] == "FBC_CONFIG_SECRET_KEY='\"1234567890\"'"
    assert plan.rebuild.only_live == plan.rebuild.only_template == []

    plan.apply(NOW)

    new = (tmp_path / "config.py").read_text()
    # The environment variable dev compose sets still works.
    assert "os.environ.get(\n    \"SQLALCHEMY_DATABASE_URI\", ''\n)" in new
    assert _settings(tmp_path) == before
    assert move.Plan(str(tmp_path)).nothing_to_do()


def test_existing_overrides_are_kept(tmp_path):
    env = "FBC_DEV_DB_PASSWORD=x\nFBC_CONFIG_SECRET_KEY='other'"
    repo = _repo(tmp_path, env=env)
    plan = move.Plan(str(repo))

    assert ("SECRET_KEY",) in plan.overridden
    assert ("SECRET_KEY",) not in plan.moved
    assert plan.changed == []

    plan.apply(NOW)

    text = (repo / "docker" / ".env").read_text()
    assert text.startswith("FBC_DEV_DB_PASSWORD=x\nFBC_CONFIG_SECRET_KEY='other'\n\n#")
    assert text.count("FBC_CONFIG_SECRET_KEY") == 1


def test_a_value_it_cant_quote_stays(tmp_path):
    live = TEMPLATE.replace('SECRET_KEY = "1234567890"', 'SECRET_KEY = "it\'s"')
    repo = _repo(tmp_path, live=live)
    plan = move.Plan(str(repo))

    assert (("SECRET_KEY",), "it has a ' or a line break in it") in plan.left
    plan.apply(NOW)
    assert 'SECRET_KEY = "it\'s"' in (repo / "config.py").read_text()


def test_dry_run_changes_nothing(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    monkeypatch.setattr(move.dump_for_dev, "REPO", str(repo))
    monkeypatch.setattr(sys, "argv", ["move_secrets_to_env.py"])

    move.main()

    assert (repo / "config.py").read_text() == LIVE
    assert not (repo / "docker" / ".env").exists()
    assert sorted(os.listdir(repo)) == ["config.py", "config.py.tpl", "docker"]
    assert "Run again with --apply" in capsys.readouterr().out


def test_refuses_when_settings_would_change(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    plan = move.Plan(str(repo))
    plan.changed = ["APP_THEME"]

    with pytest.raises(move.Failed, match="different settings"):
        plan.apply(NOW)
    assert (repo / "config.py").read_text() == LIVE


def test_env_line():
    assert move.env_line(("A",), "plain") == "FBC_CONFIG_A='plain'"
    assert move.env_line(("A", "B"), "true") == "FBC_CONFIG_A__B='\"true\"'"
    assert move.env_line(("A",), '"q"') == r"""FBC_CONFIG_A='"\"q\""'"""
    assert move.env_line(("A",), "it's") is None
