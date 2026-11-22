# ruff: noqa: E402
import importlib
import logging
import os
import sys

from flask import render_template

from flask import Flask
from flask import send_from_directory
from flask_appbuilder import AppBuilder, Model
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from fbc.core import devmode, localtime, logs, settings
from fbc.sales.index import MyIndexView

logging.basicConfig(format="%(asctime)s:%(levelname)s:%(name)s:%(message)s")
# LOG_LEVEL, INFO if unset; the eBay SDK stays at WARNING unless it's debug.
logs.init_logging()

app = Flask(__name__)
# config.py, with any FBC_CONFIG_* overrides from the environment.
settings.load(app.config)
# Before anything reads the eBay and Discogs settings (see fbc/core/devmode.py),
# and after the overrides, so it sees the final values.
devmode.init_app(app)
# The TIMEZONE setting, the localtime filters and local log times.
localtime.init_app(app)


# The image's deploy id (sha-1a2b3c4), shown in the footer. CI builds it in
# (see docker/Dockerfile); it's empty in dev and for a local build.
@app.context_processor
def revision_context():
    return {"revision": os.environ.get("FBC_REVISION", "").strip()}


# Flask-AppBuilder's page layout, with the Dev Mode banner added.
app.config.setdefault("FAB_BASE_TEMPLATE", "layout.html")
# Flask-AppBuilder's Model is the models' base class, so passing it here puts
# its metadata (ours and the ab_* tables) on db.
db = SQLAlchemy(app, model_class=Model)
migrate = Migrate(app, db)
# FBC_SKIP_VIEWS=1 is for scripts and commands that only need `app` and `db`.
# Flask-AppBuilder's security setup and the views' registration run a few
# hundred database queries on every import, which is most of the startup time.
# Anything that serves pages or needs `appbuilder` must not set it. See
# "Writing scripts" in docs/development.md.
#
# The `flask` commands (fbc/commands/) that don't need the views turn it on themselves,
# as the app is imported before Flask knows which command it will run. A value
# set in the environment, such as FBC_SKIP_VIEWS=0, wins.
SKIP_VIEWS_COMMANDS = {
    "combine-receipts",
    "backfill-earnings",
    "set-listing-skus",
    "update-listing-descriptions",
    "archive",
    "sort-other-items",
    "pictures-import",
    "sync",
    "scheduler",
}
_skip_views = os.environ.get("FBC_SKIP_VIEWS")
if _skip_views is None:
    _skip_views = "1" if SKIP_VIEWS_COMMANDS & set(sys.argv[1:5]) else ""
SKIP_VIEWS = _skip_views.lower() in ("1", "true", "yes")

if SKIP_VIEWS:
    # The views would have imported the models, and a relationship names its
    # target by string, so a query fails unless every model is loaded.
    # (import_module, so the names don't replace fbc.discogs and the like.)
    for _model_module in (
        "discogs",
        "ebay",
        "expenses",
        "inventory",
        "other",
        "pictures",
        "sales",
        "supplies",
        "sync",
    ):
        importlib.import_module("fbc.models." + _model_module)
else:
    # Flask-AppBuilder reads the config and sets up its security tables through
    # current_app, so it needs an app context, here and when adding views below.
    with app.app_context():
        appbuilder = AppBuilder(app, db.session, indexview=MyIndexView)

    @app.errorhandler(404)
    def page_not_found(e):
        return (
            render_template(
                "404.html",
                base_template=appbuilder.base_template,
                appbuilder=appbuilder,
            ),
            404,
        )


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "favicon.ico",
        mimetype="image/vnd.microsoft.icon",
    )


# Every `flask` command and group (fbc/commands/). setup-db and the commands in
# SKIP_VIEWS_COMMANDS above are the ones that care whether the views are built.
from fbc.commands.register import register

register(app)

if not SKIP_VIEWS:
    from fbc.views.menu import register_views

    register_views(app, appbuilder)
