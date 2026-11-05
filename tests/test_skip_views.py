"""FBC_SKIP_VIEWS=1 starts the app without Flask-AppBuilder's views."""

import subprocess
import sys
import textwrap

CODE = textwrap.dedent(
    """
    import sys, types
    from flask_appbuilder.security.manager import AUTH_DB

    config = types.ModuleType("config")
    config.SECRET_KEY = "x"
    config.SQLALCHEMY_DATABASE_URI = "sqlite://"
    config.AUTH_TYPE = AUTH_DB
    config.EBAY_SETTINGS = {"APP_ID": "a", "DEV_ID": "b", "CERT_ID": "c"}
    config.DISCOGS_SETTINGS = {"USER_TOKEN": "t", "USER_AGENT": "u"}
    sys.modules["config"] = config

    import fbc
    from fbc import app, db
    from fbc.models.inventory import Unit

    assert not hasattr(fbc, "appbuilder")
    assert not any("ModelView" in m for m in sys.modules if m.startswith("fbc.views"))
    assert fbc.discogs.__name__ == "fbc.discogs"
    with app.app_context():
        db.create_all()
        # Configures every mapper, so a model nobody imported would fail here.
        assert db.session.query(Unit).count() == 0
    result = app.test_cli_runner().invoke(args=["setup-db"])
    assert result.exit_code != 0 and "FBC_SKIP_VIEWS" in result.output
    print("ok")
    """
)


def test_scripts_can_use_the_models_without_the_views():
    result = subprocess.run(
        [sys.executable, "-c", CODE],
        env={"FBC_SKIP_VIEWS": "1", "PATH": ""},
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip().endswith("ok"), result.stderr


def _skip_views_for(argv, env_value=None):
    code = CODE.split("import fbc\n")[0] + (
        "import sys\nsys.argv = %r\nimport fbc\nprint(fbc.SKIP_VIEWS)\n" % (argv,)
    )
    env = {"PATH": ""}
    if env_value is not None:
        env["FBC_SKIP_VIEWS"] = env_value
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True
    )
    return result.stdout.strip().endswith("True"), result.stderr


def test_flask_commands_that_dont_need_the_views_skip_them():
    for argv in (
        ["flask", "--app", "fbc", "backfill-earnings"],
        ["flask", "combine-receipts", "--apply"],
    ):
        assert _skip_views_for(argv)[0], argv


def test_other_flask_commands_keep_the_views():
    for argv in (["flask", "--app", "fbc", "setup-db"], ["flask", "run"]):
        assert not _skip_views_for(argv)[0], argv


def test_the_environment_wins_over_the_command():
    assert not _skip_views_for(["flask", "backfill-earnings"], "0")[0]
