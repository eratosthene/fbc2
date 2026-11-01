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

Testing with Docker
-------------------

``docker-compose.dev.yml`` runs the app from this checkout's code, with
Flask's reloading dev server, against its own MariaDB 10.11 like the live
server's. It works the same on a Mac as on Linux; the image it builds is for
the machine it runs on, so build the live server's image there with
``build.sh``.

- Copy ``config.py.tpl`` to ``config.py`` if there isn't one. The app uses it
  for everything but the database; leave the eBay and Discogs keys blank
  unless you mean to reach the real sites.

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

    docker compose -f docker-compose.dev.yml up --build

The database is kept in the ``fbc2-dev_db`` volume between sessions. To start
again from the dumps, remove it::

    docker compose -f docker-compose.dev.yml down -v
