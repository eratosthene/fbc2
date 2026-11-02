# fbc

fbc keeps track of a record shop's stock and sales, and keeps them in step
with Discogs and eBay. It's a Flask-AppBuilder app on MariaDB.

- **Inventory**: Units (one copy of a record each), the Purchase Lots they
  came in and the Storage Boxes they're kept in.
- **Discogs**: the Discogs collection, synced in, with Add Unit for releases
  that have none yet.
- **Sales**: eBay listings and orders, synced in. eBay Drafts turn a Unit into
  a listing, and Ship This turns an order into a Sales Receipt.
- **Supplies**: the packing supplies sales use up, and Purchase Orders to
  restock them. Supply Use records each time stock changes, with its date:
  Ship This, Add Unit, edits to Quantity on the Supply List (a stock-take or a
  restock, which counts below 0) and Reconcile Supplies. Each supply's page
  lists its own. It starts from when this was added, with nothing before. Cost
  Per Use on a supply, set by hand, is what one use costs, for costing each
  sale's supplies.
- **Reconcile Supplies**: Ship This and Add Unit take supplies out of stock by
  estimate, so the Supply List drifts from what's on hand. Under Supplies >
  Reconcile Supplies, count what's on hand and enter it for any supplies, then
  check the differences and save them all at once. For a roll, enter how many
  are on hand, counting the one in use, and about how many uses are left on
  it. Supply Use keeps each difference apart from Supply List edits, so you
  can see which estimates drift. Purchase Orders aren't changed. Ship This's
  and Add Unit's "There aren't enough" messages and the home page's reorder
  banner link here.

## Setting up

Everything the app needs, apart from its code, is in `config.py`, or in
the environment (below). It isn't in git, since it holds the live
credentials. Copy `config.py.tpl` to start one, and fill in:

- `SECRET_KEY`: a long random string, such as the output of
  `openssl rand -hex 32`.
- `SQLALCHEMY_DATABASE_URI`: the MariaDB database.
- `EBAY_SETTINGS` and `DISCOGS_SETTINGS`: the eBay and Discogs app keys,
  and the listing defaults. The comments there say where each one comes from.
  Once the app is running, Sales > Connect eBay and Discogs > Connect Discogs
  sign in to each site, and keep the tokens in the database.

The app creates the database's tables, or brings them up to date, with:

```bash
flask --app fbc setup-db
```

and needs an admin user to sign in with:

```bash
flask --app fbc fab create-admin
```

Both run in the app's container too
(`sudo docker compose -f docker/compose.yml exec fbc flask ...`).
The container runs `setup-db` itself each time it starts.

### Settings from the environment

An environment variable named `FBC_CONFIG_<KEY>` overrides that setting in
`config.py`. It's meant for secrets and anything that differs per deploy,
so they can stay out of `config.py`:

- The value is read as JSON when it can be: `true` is a boolean and `3` a
  number. Anything else is a string.
- A double underscore reaches into a dict, and leaves its other keys alone:
  `FBC_CONFIG_EBAY_SETTINGS__APP_ID` sets only `APP_ID` in `EBAY_SETTINGS`.
- An override can turn dev mode on, but not off when `config.py` sets
  `DEV_MODE = True`. `FBC_DEV_MODE` works as before.

The live container reads them from `docker/.env`, next to
`docker/compose.yml`. Only you should be able to read it:

```bash
FBC_CONFIG_SECRET_KEY='<a long random string>'
FBC_CONFIG_SQLALCHEMY_DATABASE_URI='mysql+pymysql://fbc:<password>@<host>/fbc'
FBC_CONFIG_EBAY_SETTINGS__APP_ID='...'
FBC_CONFIG_EBAY_SETTINGS__CERT_ID='...'
FBC_CONFIG_DISCOGS_SETTINGS__CONSUMER_SECRET='...'
```

Put each value in single quotes, so Compose leaves any `$` or `#` in it
alone. A value that must stay a string but looks like a number needs
double quotes inside those: `'"12345"'`.

To move the secrets from an existing `config.py` to `docker/.env`, and
line `config.py` up with `config.py.tpl` at the same time, run this from
the repo root. On its own it only reports what it would change (`--diff`
shows the changes to `config.py`, with the secrets hidden):

```bash
scripts/move_secrets_to_env.py
```

Then make the changes, and recreate the container:

```bash
scripts/move_secrets_to_env.py --apply
docker/live-up.sh
```

Without `-n`, so the image is rebuilt: one built before settings came from
the environment ignores `docker/.env`.

The new `config.py` is `config.py.tpl` with your values in it. Settings
only the template has are left commented out, and settings only your
`config.py` has are kept. Your own comments are dropped. It checks that
the app sees exactly the same settings before and after, and changes
nothing if not. The old `config.py` is kept as `config.py.bak-<time>`; it
still holds the secrets, so delete it once the app runs.

## Running on the live server

The live server runs the app's Docker image with gunicorn, on port 20000.
Build the image there, since it's built for the machine that builds it, and
then start it, both from the repo root:

```bash
docker/build.sh
sudo docker compose -f docker/compose.yml up -d
```

`docker/live-up.sh` does both. With `-n` it skips the build and starts the
image that's already there:

```bash
docker/live-up.sh
```

To follow its logs, from the last 100 lines (`-n` picks another number, or
`all`). Ctrl-C stops following, and leaves the app running:

```bash
docker/live-logs.sh
```

To stop it and remove its container (it never removes volumes):

```bash
docker/live-down.sh
```

The Docker files are all in `docker/`. The build context is still the repo
root, since the image copies `fbc/` and `migrations/`.

The image holds no settings or secrets. The container mounts `config.py`
from the repo root, read-only, and reads `docker/.env` (see
[Settings from the environment](#settings-from-the-environment)).
`docker/live-up.sh` makes an empty `docker/.env` if there isn't one, since
the container won't start without it. After changing either file, recreate
the container, which needs no rebuild:

```bash
docker/live-up.sh -n
```

`-n` refuses to start an image built from other code than the checkout's,
since it may not read `docker/.env`; run it without `-n` then.

Keep both files readable only by you, since they hold the live credentials:

```bash
chmod 600 config.py docker/.env
```

## Development

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
  pipenv run pytest
  ```

- After changing a model, generate a migration for it, and check it over
  before committing it. The Migrations workflow runs it against MariaDB on
  the PR, and checks the result matches the models:

  ```bash
  pipenv run flask --app fbc db migrate -m "Describe the change"
  ```

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
  and an unsold Unit of a fake Discogs release.
- `fake_ebay.py listings N`: listings with an unsold Unit and no order.
- `fake_discogs.py releases N`: releases in the collection with no Units,
  each with an Add Unit link.
- `clear_fake.py`: removes every fake record, along with the Units, Sales
  Receipts and drafts made from them.

Each takes `--help`. With eBay and Discogs not connected, Ship This can't
look up the order's earnings, so enter Net Sold yourself.
