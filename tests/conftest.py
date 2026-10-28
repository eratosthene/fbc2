"""
Shared pytest fixtures for the fbc test suite.

The real app (fbc/__init__.py) builds its Flask/SQLAlchemy/AppBuilder stack at
*import time*, reading connection settings from the top-level ``config``
module -- which points at a real MySQL server with real credentials. To keep
the test suite hermetic and runnable offline, we install an in-memory SQLite
stand-in for ``config`` in ``sys.modules`` *before* ``fbc`` is ever imported,
so ``app.config.from_object("config")`` picks up the fake module instead of
reading config.py.
"""

import logging
import sys
import types

import pytest


def _install_fake_config():
    from flask_appbuilder.security.manager import AUTH_DB

    config = types.ModuleType("config")
    config.SECRET_KEY = "test-secret-key"
    config.SQLALCHEMY_DATABASE_URI = "sqlite://"
    config.SQLALCHEMY_TRACK_MODIFICATIONS = False
    config.CSRF_ENABLED = False
    config.WTF_CSRF_ENABLED = False
    config.APP_NAME = "FBC-Test"
    config.AUTH_TYPE = AUTH_DB
    config.AUTH_USER_REGISTRATION = False
    config.EBAY_SETTINGS = {
        "APP_ID": "test-app-id",
        "DEV_ID": "test-dev-id",
        "CERT_ID": "test-cert-id",
        "USER_TOKEN": "test-user-token",
    }
    config.DISCOGS_SETTINGS = {
        "USER_TOKEN": "test-discogs-token",
        "USER_AGENT": "fbc-tests/1.0",
    }
    sys.modules["config"] = config


# Keep AppBuilder's permission-registration logging out of the test output.
logging.getLogger().setLevel(logging.ERROR)

_install_fake_config()

import fbc  # must be imported only after the fake config is installed

# fbc/__init__.py calls db.create_all() before the model modules (which live
# under fbc.models and get imported further down in that same file) have
# been loaded, so the first create_all() only creates Flask-AppBuilder's own
# security tables. By the time this module finishes importing `fbc`, all of
# our own models have been registered on the metadata too, so calling
# create_all() again fills in the rest.
fbc.db.create_all()


@pytest.fixture
def app():
    """The single, module-level Flask application under test."""
    return fbc.app


@pytest.fixture
def db():
    """The single, module-level Flask-SQLAlchemy extension under test."""
    return fbc.db


@pytest.fixture(autouse=True)
def app_context():
    """
    Give every test an application/request context (so things like
    ``url_for`` work) and a clean, isolated database.
    """
    with fbc.app.test_request_context():
        fbc.db.session.remove()
        fbc.db.drop_all()
        fbc.db.create_all()
        try:
            yield fbc.app
        finally:
            fbc.db.session.remove()
