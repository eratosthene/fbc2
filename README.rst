fbc
===

fbc keeps track of a record shop's stock and sales, and keeps them in step
with Discogs and eBay. It's a Flask-AppBuilder app on MariaDB.

- **Inventory**: Units (one copy of a record each), the Purchase Lots they
  came in and the Storage Boxes they're kept in.
- **Discogs**: the Discogs collection, synced in, with Add Unit for releases
  that have none yet.
- **Sales**: eBay listings and orders, synced in. eBay Drafts turn a Unit into
  a listing, and Ship This turns an order into a Sales Receipt.
- **Supplies**: the packing supplies sales use up, and Purchase Orders to
  restock them.

Setting up
----------

Everything the app needs, apart from its code, is in ``config.py``. It isn't
in git, since it holds the live credentials. Copy ``config.py.tpl`` to start
one, and fill in:

- ``SECRET_KEY``: a long random string, such as the output of
  ``openssl rand -hex 32``.
- ``SQLALCHEMY_DATABASE_URI``: the MariaDB database.
- ``EBAY_SETTINGS`` and ``DISCOGS_SETTINGS``: the eBay and Discogs app keys,
  and the listing defaults. The comments there say where each one comes from.
  Once the app is running, Sales > Connect eBay and Discogs > Connect Discogs
  sign in to each site, and keep the tokens in the database.

The app creates the database's tables, or brings them up to date, with::

    flask --app fbc setup-db

and needs an admin user to sign in with::

    flask --app fbc fab create-admin

Both run in the app's container too (``docker compose exec fbc flask ...``).
The container runs ``setup-db`` itself each time it starts.

Running on the live server
--------------------------

The live server runs the app's Docker image with gunicorn, on port 20000.
Build the image there, since it's built for the machine that builds it, and
then start it::

    ./build.sh
    sudo docker compose up -d

The image has ``config.py`` built into it, so a change to it needs a
rebuild too.

Development
-----------

- Install the dependencies (including the dev ones) from ``Pipfile.lock``::

    pipenv install --dev

- Install the git hooks, once per clone. They run Ruff, the filesystem checks
  and the test suite on every commit::

    pre-commit install

- After changing ``Pipfile.lock``, regenerate the ``requirements.txt`` the
  Docker image installs from (CI checks the two match)::

    pipenv requirements > requirements.txt

- To run the app on this machine, with Flask's reloading dev server and the
  database in ``config.py``, then open http://127.0.0.1:5556::

    dev-scripts/run-local.sh

  ``-H`` picks the address to listen on (a LAN address, or ``0.0.0.0``, to
  reach it from other machines) and ``-p`` the port; ``FBC_HOST`` and
  ``FBC_PORT`` do the same. Its port isn't 5555, so it can run alongside the
  Docker setup below.

- Run the tests::

    pipenv run pytest

- After changing a model, generate a migration for it, and check it over
  before committing it. The Migrations workflow runs it against MariaDB on
  the PR, and checks the result matches the models::

    pipenv run flask --app fbc db migrate -m "Describe the change"

Testing with Docker
-------------------

``docker-compose.dev.yml`` runs the app from this checkout's code, with
Flask's reloading dev server, against its own MariaDB 10.11 like the live
server's. It works the same on a Mac as on Linux; the image it builds is for
the machine it runs on, so build the live server's image there with
``build.sh``.

- Copy ``config.py.tpl`` to ``config.py`` if there isn't one. The app uses it
  for everything but the database.

- The app runs in dev mode here, with a banner on every page saying so. It
  doesn't use the eBay and Discogs keys in ``config.py`` or the tokens saved
  in the database, so a copy of live data can't reach the live accounts.
  Syncs log "Dev Mode is turned on, external syncing disabled" and finish
  without doing anything, and picture uploads, Check with eBay, Publish,
  Shipping & Returns and Connect eBay/Discogs are turned off. Ship This
  records the Sales Receipt as usual, but takes Net Sold from you and only
  removes the Discogs copy from fbc. To reach the real sites on purpose, put
  ``FBC_DEV_MODE=0`` in ``.env`` (and leave ``DEV_MODE`` out of
  ``config.py``, or set it to ``False``).

- Put a password for the database in ``.env``::

    echo "FBC_DEV_DB_PASSWORD=$(openssl rand -hex 16)" > .env

  To use the database from ``flask`` on this machine as well, point
  ``config.py``'s ``SQLALCHEMY_DATABASE_URI`` at
  ``mysql+pymysql://fbc:<that password>@127.0.0.1:3307/fbc``.

- Optionally, put ``.sql`` dumps (``mysqldump`` output) in ``dev-db/``. They
  are loaded the first time the database starts. Without one the app builds
  an empty database, and needs an admin user::

    docker compose -f docker-compose.dev.yml exec app flask fab create-admin

- Start it, then open http://127.0.0.1:5555::

    dev-scripts/compose-up.sh

  It runs in the foreground, with the logs, until Ctrl-C. With ``-d`` it
  starts in the background, and says how to follow the logs and stop it.

The database is kept in the ``fbc2-dev_db`` volume between sessions. To start
again from the dumps, remove it::

    docker compose -f docker-compose.dev.yml down -v

Fake eBay and Discogs records
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``fake_*.py`` scripts in ``dev-scripts/`` add fake records in place of a sync from eBay
or Discogs, to try things like Ship This and Add Unit without the real sites.
They only run when ``config.py`` sets ``ALLOW_FAKE_DATA = True`` and the
database is on this machine or in ``docker-compose.dev.yml``, and they aren't
in the Docker image. Run them from the repo root with ``pipenv run python``,
or in the app container::

    docker compose -f docker-compose.dev.yml exec app python dev-scripts/fake_ebay.py orders 5

- ``fake_ebay.py orders N``: orders that each offer Ship This, with a listing
  and an unsold Unit of a fake Discogs release.
- ``fake_ebay.py listings N``: listings with an unsold Unit and no order.
- ``fake_discogs.py releases N``: releases in the collection with no Units,
  each with an Add Unit link.
- ``clear_fake.py``: removes every fake record, along with the Units, Sales
  Receipts and drafts made from them.

Each takes ``--help``. With eBay and Discogs not connected, Ship This can't
look up the order's earnings, so enter Net Sold yourself.
