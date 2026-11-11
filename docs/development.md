# Developing fbc

How to work on the app: run it on this machine, test it, and try it against
a copy of live data. For the live server, see
[Running fbc in production](production.md).

## Setting up

The app reads its settings from `config.py`, which isn't in git. Copy
`config.py.tpl` to start one; [Setting up](production.md#setting-up) says
what goes in it. A worktree has none, so link the main checkout's:

```bash
ln -s "$(git rev-parse --git-common-dir)/../config.py" .
```

- Install the dependencies (including the dev ones) from `Pipfile.lock`:

  ```bash
  pipenv install --dev
  ```

- Install the git hooks, once per clone. They run Ruff, mdformat and the
  file checks on every commit, and the test suite before every push:

  ```bash
  pre-commit install
  ```

- To run the app on this machine, with Flask's reloading dev server and the
  database in `config.py`, then open http://127.0.0.1:5556:

  ```bash
  dev-scripts/run-local.sh
  ```

  `-H` picks the address to listen on (a LAN address, or `0.0.0.0`, to
  reach it from other machines) and `-p` the port; `FBC_HOST` and
  `FBC_PORT` do the same. Its port isn't 5555, so it can run alongside the
  Docker setup below.

- Run the tests:

  ```bash
  pipenv run pytest -n auto
  ```

  `-n auto` runs them in parallel, one worker per CPU (pytest-xdist), which is
  about three times faster. Leave it off for one test or a failure you want to
  read in order, since starting the workers costs a couple of seconds.

- After changing a model, generate a migration for it, and check it over
  before committing it. The Migrations workflow runs it against MariaDB on
  the PR, and checks the result matches the models:

  ```bash
  pipenv run flask --app fbc db migrate -m "Describe the change"
  ```

## Writing scripts

`import fbc` builds the whole web app: Flask-AppBuilder sets up its security
tables and registers every view, which is the pile of `Registering route ...`
lines. That is about 550 database queries, and it takes most of the startup
time. A script that only reads or writes the database, or calls the sync code,
doesn't need any of it.

Set `FBC_SKIP_VIEWS=1` and `import fbc` skips the views. `app`, `db`, the
models and everything outside `fbc/views/` still work; `fbc.appbuilder` and
the pages don't exist. The scripts in `scripts/` and `dev-scripts/`, and the
`combine-receipts` and `backfill-earnings` commands, already turn it on (a
value in the environment, such as `FBC_SKIP_VIEWS=0`, wins). A new script
sets it before importing `fbc`:

```python
os.environ.setdefault("FBC_SKIP_VIEWS", "1")
from fbc import app, db
```

A new `flask` command that doesn't need the views goes in
`SKIP_VIEWS_COMMANDS` in `fbc/__init__.py`; it can't set the variable itself,
as the app is imported first.

Timings, in dev mode on a Mac against SQLite, with extra delay per query to
stand in for a database over a network:

| Delay per query | Full import | `FBC_SKIP_VIEWS=1` |
| --------------- | ----------- | ------------------ |
| none            | 0.6 s       | 0.3 s              |
| 1 ms            | 1.5 s       | 0.3 s              |
| 3 ms            | 3.2 s       | 0.3 s              |

So the saving is the queries: more on the live server's MariaDB than here.
What is left is importing Flask, SQLAlchemy and Flask-AppBuilder.

Leave it off for the web server, `flask setup-db` (it refuses; it needs
Flask-AppBuilder's tables) or `flask db migrate` (it needs them to compare).
New command-line jobs, such as sync commands and `flask set-listing-skus`,
should use it: take `app` and `db` from `fbc`, and don't touch
`appbuilder`. A script that needs the web app, such as a test client, leaves
it off.

## Shipment tracking

`fbc/tracking.py` reads each order's carrier and tracking number from the
`GetOrders` sync (`ShipmentTrackingDetails`, which needs no new OAuth scope)
and links to the carrier's page. It doesn't ask for tracking progress: eBay
doesn't give it, and a carrier's API would. USPS's Tracking API was tried and
dropped, because it needs a credit card on file with USPS; the
[Tracking 3.2 spec](https://developers.usps.com/sites/default/files/apidoc_specs/tracking-v3r2_7.yaml)
is `POST /tracking/v3r2/tracking` with a list of tracking numbers, if that
changes. Any such client would keep its state on `EbayOrder` and go through
`fbc/devmode.py`'s guards.

## Active listings and free insertions

`EbayListing.ended` and `end_time` are set only by the listings sync, through
`util.mark_ended_listings`, after `listing.fetch_active_items` has read the
whole active list (it raises unless every page came back and the items match
eBay's `TotalNumberOfEntries`, and a failed sync records nothing).
`sync.record_finished` and `sync.last_finished` keep when a sync last
finished in the `sync_record` table, by name; the listings sync's is
`sync.LISTINGS_SYNC`. Unlike the job files, it lasts.

`fbc/insertions.py` holds the free-insertions maths in one place, with its
assumptions in the docstring (Pacific calendar month, 30-day renewals, ended
listings counted up to `end_time`). Change them there and in
`tests/test_insertions.py`. Pacific time is worked out by hand, because the
Alpine image has no tzdata for `zoneinfo`.

`insertions.calibrate` stores the eBay app's number as an adjustment in
`insertion_calibration`, one row per month keyed by the month's start, so a
new month ignores last month's row. `count_insertions` adds it to `used`.

Nothing here reaches eBay except the listings sync, which dev mode skips like
the other syncs. There is no quick count refresh on the home page: the page
only reads the database.

## Testing with Docker

`docker/compose.dev.yml` runs the app from this checkout's code, with
Flask's reloading dev server, against its own MariaDB 10.11 like the live
server's. It works the same on a Mac as on Linux; the image it builds is for
the machine it runs on, so build the live server's image there with
`docker/build.sh`. Its `.env` and `dev-db/` are in `docker/`, next to
it, since Compose reads `.env` from the compose file's folder.

- Copy `config.py.tpl` to `config.py` if there isn't one. The app uses it
  for everything but the database. `FBC_CONFIG_*` overrides in
  `docker/.env` work here too.

- The app runs in dev mode here, with a banner on every page saying so. It
  doesn't use the eBay and Discogs keys in `config.py` or the tokens saved
  in the database, so a copy of live data can't reach the live accounts.
  Syncs log "Dev Mode is turned on, external syncing disabled" and finish
  without doing anything, and picture uploads, Check with eBay, Publish,
  Shipping & Returns and Connect eBay/Discogs are turned off. Ship This
  records the Sales Receipt as usual, but takes Net Sold from you and only
  removes the Discogs copy from fbc. To reach the real sites on purpose, put
  `FBC_DEV_MODE=0` in `docker/.env` (and leave `DEV_MODE` out of
  `config.py`, or set it to `False`).

- Put a password for the database in `docker/.env`:

  ```bash
  echo "FBC_DEV_DB_PASSWORD=$(openssl rand -hex 16)" > docker/.env
  ```

  To use the database from `flask` on this machine as well, point
  `config.py`'s `SQLALCHEMY_DATABASE_URI` at
  `mysql+pymysql://fbc:<that password>@127.0.0.1:3307/fbc`.

- Optionally, put dumps (`.sql` or `.sql.gz`) in `docker/dev-db/`, such
  as a copy of live data (below). They are loaded the first time the database
  starts. Without one the app builds an empty database, and needs an admin
  user:

  ```bash
  docker compose -f docker/compose.dev.yml exec app flask fab create-admin
  ```

- Start it, then open http://127.0.0.1:5555:

  ```bash
  dev-scripts/compose-up.sh
  ```

  It runs in the foreground, with the logs, until Ctrl-C. With `-d` it
  starts in the background, and says how to follow the logs and stop it.

- To stop it and remove its containers:

  ```bash
  dev-scripts/compose-down.sh
  ```

The database is kept in the `fbc2-dev_db` volume between sessions, and
`compose-down.sh` never removes it. To start again from the dumps in
`docker/dev-db/`, empty the database and load them into it. It asks first,
since it deletes the database you had; `-y` skips that:

```bash
dev-scripts/compose-reset-db.sh
```

It drops and recreates the database inside the dev MariaDB, so the volume
stays. If the app is running, it stops it first and starts it again after.
`compose-down.sh` and `compose-reset-db.sh` only act on a compose project of
`docker/compose.dev.yml` on this machine: they refuse a remote `DOCKER_HOST`
or docker context. `-p` points them at a throwaway project instead of
`fbc2-dev`, such as `fbc2-test`.

### A proxy that re-signs HTTPS

Some company networks (Zscaler, for one) re-sign every HTTPS request, so
`pip install` and `apk add` fail while the image builds, with certificate
errors. To fix that in the dev image only, put the proxy's root CA in
`docker/dev-certs/` as a `.pem` or `.crt` file. On a Mac, if the CA is in the
System keychain:

```bash
security find-certificate -a -c Zscaler -p /Library/Keychains/System.keychain > docker/dev-certs/zscaler.pem
```

Copy the file into the folder; don't symlink it. The build can't follow a
symlink out of the folder, and fails with a message if it finds one. A
worktree has its own `docker/dev-certs/`, so copy it there too:
`cp <main checkout>/docker/dev-certs/zscaler.pem docker/dev-certs/`.

Then rebuild: `docker compose -f docker/compose.dev.yml build app`. The image
trusts the CA in its system store, in pip's settings and in certifi's bundle
(which `requests` uses). The files are ignored by git. `docker/build.sh` and
the CI image don't read the folder, so the live image is the same as before.

Docker pulling `python` and `mariadb` is the Docker daemon's job, not the
image's, so the CA also has to be trusted by Docker Desktop itself.

### A copy of live data

`scripts/dump_for_dev.py` makes a dump of the live database to load here,
with what's sensitive taken out:

- The eBay and Discogs tokens, Flask-AppBuilder's API keys and pending
  sign-ups, and the app's users (`ab_user`, `ab_user_role` and
  `ab_user_group`) have their tables but no rows.
- Buyers' eBay usernames on eBay orders become `buyer-1`, `buyer-2` and so
  on, each buyer keeping the same one.

Everything else is kept, roles and permissions and `alembic_version` too,
so `flask setup-db` brings it up to date when the app starts.

1. On the live server, from the repo root:

   ```bash
   scripts/dump_for_dev.py --admin-user fbc_dump
   ```

   It dumps the database the app uses, `FBC_CONFIG_SQLALCHEMY_DATABASE_URI`
   from `docker/.env` if it's there, or else `config.py`'s
   `SQLALCHEMY_DATABASE_URI`, to `fbc-dev-<date>.sql.gz`. The eBay orders, whose buyers it changes, are
   first copied into a scratch database (`fbc_dev_scrub`) on the same
   server and scrubbed there; everything else comes straight from live, in
   one snapshot taken just after. Each step says how long it took, and the
   long ones show which table they're on and how many MB they've done so
   far. The fbc user can't create databases, so `--admin-user` names a
   MariaDB user that can, and asks for its password. `-o` picks the folder to write to. It needs
   Python 3 and the MariaDB client (`mariadb` and `mariadb-dump`, or
   MariaDB's `mysql` and `mysqldump` where it only has those names), but not
   the app's packages.

   The first time, make the `fbc_dump` user it signs in as. It can do
   anything to the scratch database and nothing else, so it can't read or
   change the live one. As root on the live server (`mysql -u root -p`):

   ```sql
   CREATE USER 'fbc_dump'@'<address>' IDENTIFIED BY '<password>';
   GRANT ALL PRIVILEGES ON `fbc_dev_scrub`.* TO 'fbc_dump'@'<address>';
   ```

   `<address>` is where MariaDB sees the script connect from: the server's
   own address, when `SQLALCHEMY_DATABASE_URI` connects to it, or
   `localhost` when it uses the socket. If it's wrong, the script says
   "Access denied for user 'fbc_dump'@'…'", with the address to use.

   The live server's password policy wants mixed case, digits and a symbol,
   which `openssl rand -hex` doesn't give. This makes a password with all
   of them, using only symbols that need no quoting in the SQL above:

   ```bash
   python3 -c 'import secrets, string
   symbols = "-_.+"
   chars = string.ascii_letters + string.digits + symbols
   while True:
       p = "".join(secrets.choice(chars) for _ in range(24))
       if (any(c.islower() for c in p) and any(c.isupper() for c in p)
               and sum(c.isdigit() for c in p) >= 2 and any(c in symbols for c in p)):
           print(p)
           break'
   ```

   Before finishing, it checks the dump has every table once, no rows of the
   emptied tables, and none of the live tokens, password hashes, API keys
   and user emails, and deletes it if not.

2. Copy it into `docker/dev-db/` here, and remove any older dumps there,
   since they'd all be loaded:

   ```bash
   scp <live server>:fbc-dev-<date>.sql.gz docker/dev-db/
   ```

3. Empty the database and load the dump into it. This removes the
   database you had, and any changes made in it. Then start the app, if it
   isn't running, to bring the dump up to date:

   ```bash
   dev-scripts/compose-reset-db.sh
   dev-scripts/compose-up.sh
   ```

4. The dump has no users, so create an admin to sign in with:

   ```bash
   docker compose -f docker/compose.dev.yml exec app flask fab create-admin
   ```

The app runs in dev mode, and the dump has no tokens, so syncs and the eBay
and Discogs actions do nothing.

The Dev dump workflow runs the script on a PR that changes it, against a
MariaDB seeded with tokens, users and buyers, and checks the dump it makes.

### Fake eBay and Discogs records

The `fake_*.py` scripts in `dev-scripts/` add fake records in place of a sync from eBay
or Discogs, to try things like Ship This and Add Unit without the real sites.
They only run when `config.py` sets `ALLOW_FAKE_DATA = True` and the
database is on this machine or in `docker/compose.dev.yml`, and they aren't
in the Docker image. Run them from the repo root with `pipenv run python`,
or in the app container:

```bash
docker compose -f docker/compose.dev.yml exec app python dev-scripts/fake_ebay.py orders 5
```

- `fake_ebay.py orders N`: orders that each offer Ship This, with a listing
  and an unsold Unit of a fake Discogs release. `--items 3` puts three
  items on each order, and `--buyer someone` gives every order the same
  buyer, so Ship This offers to ship them combined.
- `fake_ebay.py listings N`: listings with an unsold Unit and no order.
- `fake_discogs.py releases N`: releases in the collection with no Units,
  each with an Add Unit link.
- `clear_fake.py`: removes every fake record, along with the Units, Sales
  Receipts and drafts made from them.

Each takes `--help`. With eBay and Discogs not connected, Ship This can't
look up the order's earnings, so enter Net Sold yourself.
