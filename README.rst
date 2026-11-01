Base Skeleton to start your application using Flask-AppBuilder
--------------------------------------------------------------

- Install it::

	pip install flask-appbuilder
	git clone https://github.com/dpgaspar/Flask-AppBuilder-Skeleton.git

- Run it::

    $ export FLASK_APP=app
    # Create an admin user
    $ flask fab create-admin
    # Run dev server
    $ flask run


That's it!!

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
