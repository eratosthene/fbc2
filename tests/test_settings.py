import importlib.util
import os

from flask import Config

from fbc import settings


class _Source:
    """Stands in for config.py."""

    SECRET_KEY = "from-config"
    DEV_MODE = False
    EBAY_SETTINGS = {"APP_ID": "config-app", "LISTING": {"CATEGORY_ID": "176985"}}


def _load(source=_Source):
    config = Config(".")
    settings.load(config, source)
    return config


def test_config_alone():
    config = _load()

    assert config["SECRET_KEY"] == "from-config"
    assert config["EBAY_SETTINGS"]["APP_ID"] == "config-app"


def test_the_environment_overrides_a_setting(monkeypatch):
    monkeypatch.setenv("FBC_CONFIG_SECRET_KEY", "from-env")
    monkeypatch.setenv("FBC_CONFIG_APP_THEME", "slate.css")
    monkeypatch.setenv("FBC_CONFIG_ALLOW_FAKE_DATA", "true")

    config = _load()

    assert config["SECRET_KEY"] == "from-env"
    assert config["APP_THEME"] == "slate.css"
    # Read as JSON, so it's a boolean rather than the string "true".
    assert config["ALLOW_FAKE_DATA"] is True


def test_the_environment_overrides_one_key_in_a_dict(monkeypatch):
    class Source(_Source):
        EBAY_SETTINGS = {"APP_ID": "config-app", "LISTING": {"CATEGORY_ID": "1"}}

    monkeypatch.setenv("FBC_CONFIG_EBAY_SETTINGS__APP_ID", "env-app")
    monkeypatch.setenv("FBC_CONFIG_EBAY_SETTINGS__LISTING__POSTAL_CODE", '"12345"')

    config = _load(Source)

    assert config["EBAY_SETTINGS"] == {
        "APP_ID": "env-app",
        # Quoted, so it stays a string rather than a number.
        "LISTING": {"CATEGORY_ID": "1", "POSTAL_CODE": "12345"},
    }


def test_the_environment_turns_dev_mode_on(monkeypatch):
    monkeypatch.setenv("FBC_CONFIG_DEV_MODE", "true")

    assert _load()["DEV_MODE"] is True


def test_the_environment_cant_turn_off_dev_mode_set_in_config(monkeypatch):
    class Source(_Source):
        DEV_MODE = True

    monkeypatch.setenv("FBC_CONFIG_DEV_MODE", "false")

    assert _load(Source)["DEV_MODE"] is True


def test_other_fbc_variables_arent_settings(monkeypatch):
    monkeypatch.setenv("FBC_DEV_MODE", "0")
    monkeypatch.setenv("FBC_DEV_DB_PASSWORD", "pw")

    config = _load()

    assert config["DEV_MODE"] is False
    assert "DEV_DB_PASSWORD" not in config


def test_fake_data_scripts_check_the_overridden_settings(monkeypatch):
    """dev-scripts/_fake.py checks the database the app will use, not just
    config.py's."""
    path = os.path.join(os.path.dirname(__file__), "..", "dev-scripts", "_fake.py")
    spec = importlib.util.spec_from_file_location("_fake", path)
    fake = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fake)
    monkeypatch.setenv("FBC_CONFIG_SQLALCHEMY_DATABASE_URI", "mysql://fbc@live/fbc")

    config = fake._settings()

    assert config["SQLALCHEMY_DATABASE_URI"] == "mysql://fbc@live/fbc"
