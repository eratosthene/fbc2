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
`combine-receipts`, `backfill-earnings` and `sync` commands, already turn it on (a
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

## Running the syncs from the command line

Each sync the Sync buttons run has a `flask sync` command. The command and
the button call the same function in `fbc/syncjobs.py`, so they do the same
thing. A new sync goes there too, with a command beside the others in
`fbc/__init__.py`.

| Command                           | Button                                                                          |
| --------------------------------- | ------------------------------------------------------------------------------- |
| `flask sync discogs`              | Sync Discogs                                                                    |
| `flask sync discogs-listings`     | Sync on the Discogs Listings list (reads the Marketplace inventory)             |
| `flask sync ebay-listings`        | Sync eBay listings                                                              |
| `flask sync ebay-orders`          | Sync eBay orders (adds new orders, and checks ones without items or tracking)   |
| `flask sync ebay-orders --update` | Sync eBay orders with `?doupdate=True`, which also refreshes the orders fbc has |
| `flask sync ebay-orders --deep`   | Deep sync, looking back 88 days, not 10                                         |

```bash
pipenv run flask --app fbc sync ebay-orders --deep
```

The command prints the log lines the button's window shows, and exits 1 if
the sync failed, logged an error and carried on (some syncs do, such as eBay
not being connected), or couldn't start. It couldn't start when the same sync
is already running, from a button or another command, even in another
process: a command and a button for the same sync never run together. The
two order syncs count as one sync, so `ebay-orders` and
`ebay-orders --deep` also keep out of each other's way. A command's job shows
up to the Sync buttons the same way, so a button pressed meanwhile joins its
log.

The job files (`fbc/sync.py`) are in `SYNC_JOB_FOLDER`, which defaults to a
folder in the system temp folder. A command and the web server have to use the
same folder to see each other, so run commands in the same container as the
web server.

Each sync records when it last finished successfully: `discogs`,
`discogs-listings`, `ebay-orders` (either order sync) and `ebay-listings`, in the `sync_record`
table. `sync.last_finished(name)` reads it. A sync that fails records
nothing.

In dev mode a command logs "Dev Mode is turned on, external syncing
disabled" and finishes without reaching eBay or Discogs, as the buttons do.
The commands skip view registration (see Writing scripts), so they start in
about a second.

## The scheduler and the tasks lists

`flask scheduler` (`fbc/scheduler.py`) runs the syncs on intervals. It's a
Flask command rather than cron or a shell loop because it reuses the app's
settings and dev mode check, runs the same jobs as the buttons (`fbc/syncjobs.py`)
and logs to the container's output. `docker/docker-entrypoint.sh` starts it in
the background, next to gunicorn, only when the container is running gunicorn:
the jobs' lock is in that container's job files and PIDs. The entrypoint
starts it again 30 seconds after a crash. The scheduler exits 0 when it's off,
which ends that loop.

Every minute it asks `sync.last_finished` what is due. A sync is due when it
has never finished, or finished more than its interval ago, so a sync run by
hand counts. Each runs in the scheduler's process, one at a time, orders
first. A sync that is already running (`sync.Busy`) is skipped until the next
minute. A sync that failed records nothing, so the scheduler remembers the
failure in memory and waits `SCHEDULER_RETRY_MINUTES` before trying it again.
The scheduler runs `ebay-orders` without `--update`; refreshing old orders
stays a manual job.

The settings are in `config.py.tpl`: `SCHEDULER` (`False` turns it off), and
`SCHEDULER_EBAY_ORDERS_MINUTES`, `SCHEDULER_EBAY_LISTINGS_MINUTES`,
`SCHEDULER_DISCOGS_MINUTES`, `SCHEDULER_DISCOGS_LISTINGS_MINUTES` and
`SCHEDULER_RETRY_MINUTES`. In dev mode it logs
that scheduled syncs are off and exits, so it never reaches eBay or Discogs,
and `docker/compose.dev.yml` runs `flask run`, so it doesn't start one at all.
To try it by hand, run `flask scheduler` with a short interval in a throwaway
setup; with `FBC_DEV_MODE=1` it does nothing.

The Tasks menu and the home page's To do banner (`fbc/tasks.py`) have no table
of their own: each task is a query that stops matching once the work is done.
The lists are the existing ones with a filter (`fbc/views/tasks.py`), and eBay
Drafts to List (eBay) and Drafts to List (Discogs) are filter links on the eBay Drafts and Discogs Listings lists. `fbc/tasks.py` says
what each one means. A new kind of task is a new function there, a view or
filter link, and a count for the banner.

## Calling Discogs

`fbc/discogs_client.py` is the only module that imports `python3-discogs-client`, and
every call fbc makes to Discogs is a function or an `Account` method in it:
`release()`, `master()`, `search()`, `account().collection_items()`,
`account().create_listing()` and so on. They take plain values and return the
JSON Discogs sent, as dicts and lists, so nothing else in fbc sees the
library's objects. Errors come out as `discogs.ApiError` (with `status_code`,
and `refused` and `rate_limited` for the common cases), and a list that wasn't
read in full as `discogs.IncompleteRead`. Anything else, such as a timeout, is
passed on as it is.

To call Discogs from new code, add a function there, with a test in
`tests/test_discogs_seam.py`, which fakes the library and checks what is sent.
Every other test fakes `fbc.discogs_client` itself with `tests/fake_discogs.py`
(`FakeDiscogs` and `FakeAccount`), so a test never knows which library is
behind it. That also makes it a single place to change if fbc ever moves to
another library.

## The Discogs Marketplace

Notes for selling on Discogs (issue #63), found while building the Discogs
listings sync. They come from the `discogs_client` 2.10 source and a mirror of
Discogs's Marketplace OpenAPI spec
(`artifacts.apis.io/discogs/openapi/discogs-marketplace-api-openapi.yml`).
The official docs (`discogs.com/developers`) refuse automated requests, and
nothing here was run against Discogs, so check anything that matters against
a real response before building on it.

**Pictures.** A listing can't have pictures set through the API. The listing
fields to create or edit are release, condition, sleeve condition, price,
comments, allow offers, status, external ID, location, weight and format
quantity. There is no photo field, and no endpoint for one among the
Marketplace operations. The website's Add Photos button on a seller's item has
no API behind it. Pictures for a Discogs listing are added by hand on the
site, so a local copy of a Unit's pictures (#140) can't be pushed to Discogs.

**Inventory.** `GET /users/{username}/inventory`, which is
`Account.inventory()` in `fbc/discogs_client.py`. It returns `pagination` and `listings`.
The `status` filter defaults to **For Sale**, so Drafts and Expired items don't
come back unless it's asked for. Its values are For Sale, All, Draft, Expired,
Sold, Violation, Suspended and Deleted. (The sync asks for All.) It pages from
page 1, up to 100 a page, and `pagination.items` is the item count (the read
fails with `IncompleteRead` if fewer items come back). It sorts by listed, price, item, artist, label, catno, audio, status or location.
Someone other than the owner sees only For Sale items.

**Listings.** A listing's JSON has `id` (a big number), `status`, `price`
(`value` and `currency`), `condition`, `sleeve_condition`, `posted`
(ISO 8601 with a zone), `uri`, `release` (`id` and a `description` like
"Artist - Title"), `comments`, `allow_offers`, `ships_from`, `weight` (grams),
`format_quantity`, `external_id` and `location`. The release can be one that
isn't in your collection. Only For Sale, Draft and Expired listings can be
edited; others can only be deleted. A new listing's status is For Sale or
Draft.

**`external_id` and `location`.** Both are free text set by the seller. The
spec describes `external_id` as the seller's private comments, which is where
Discogs shows it, so someone can type over it by hand. fbc will write
`fbc-<Unit ID>` there, the same as eBay's SKU (`listing.sku`,
`listing.unit_id_from_sku`), and treat anything else as "no Unit". `location`
is the Storage Box's place for the Unit.

**Fees and earnings.** `client.fee_for(price)` gives Discogs's selling fee
for a price (`GET /marketplace/fee/{price}`, as a `Price`). An order has `fee`
(Discogs's fee), `total`, `shipping`, `items`, `status`, `next_status`,
`created`, `last_activity`, `buyer`, `additional_instructions` and messages.
Nothing in it says what payment processing took, so Net Sold from a Discogs
order is the total less that fee, and the payment fee has to be typed in or
left out. Order statuses are New Order, Buyer Contacted, Invoice Sent, Payment
Pending, Payment Received, In Progress, Shipped, Refund Sent and Cancelled.

**What the sync does.** `syncjobs.sync_discogs_listings` reads the whole
inventory (checking it got as many items as `len(inventory)` says), saves each
as a `DiscogsListing` (`util.save_discogs_listing`), links it to its Unit by
`external_id` if the Unit exists, and then marks every listing not in that read
as ended (`util.mark_ended_discogs_listings`). A listing whose status is Sold,
Deleted, Violation or Suspended is ended too (`util.DISCOGS_ENDED_STATUSES`):
Discogs lets only For Sale, Draft and Expired listings be edited. It only
reads, and it goes through `fbc/discogs_client.py`, so dev mode stops it. The `sync_record` name is `discogs-listings`. There's no Task for it
yet.

**Listing a Unit.** `fbc/discogs_listing.py` holds the only writes to Discogs's
Marketplace, and `DiscogsListingModelView` (`from_unit`, `make_for_sale`) has
the pages. `discogs_client` 2.10 does have `identity().inventory.add_listing(...)`, but it
throws away Discogs's answer, so the new listing's ID is lost. fbc needs the
ID to save the row, so `Account.create_listing` posts to
`/marketplace/listings` with the library's private `_post`, which returns it,
as `Account.add_to_collection` does for the collection. Only `fbc/discogs_client.py`
does that, so a library that returns the ID would be a change there. The POST
fields are `release_id`,
`condition`, `sleeve_condition`, `price`, `comments`, `allow_offers`, `status`,
`external_id`, `location` and `weight` (grams), and it answers `listing_id`.
Like the rest of the notes above, this comes from the spec, and wasn't run
against Discogs, so check it on the first real Draft. Notes:

- A Draft first, then `make_for_sale`, which reads the listing, checks it's
  still a Draft and POSTs `release_id`, `condition`, `price` and
  `status: For Sale` to `/marketplace/listings/{id}` (the edit call wants the
  first three again).
- Every write goes through `fbc/discogs_client.py`, which raises in dev mode, and
  the views check `devmode.on()` first as well.
- After a create, the row is saved from what was posted
  (`util.save_discogs_listing`, the same function the sync uses), so a later
  sync updates it by `listing_id`. `external_id` is `listing.sku(unit.id)` and
  is read back with `listing.unit_id_from_sku`, as for eBay.
- A 4xx from Discogs (not 429) is a refusal: nothing is saved. A timeout, a
  dropped connection, a 5xx or an answer with no `listing_id` raises
  `PostUncertain`. The create always reads the newest `RECENT_ITEMS` items of
  the inventory first (`find_existing`), so a retry adopts a listing a
  timed-out post made. An older listing is found through the database
  (`open_listing`) instead.
- A Unit needs a collection instance (`discogs_instance_id`) to be listed.
- `Unit.grading` maps to Discogs's condition names in
  `CONDITION_FOR_GRADE`; EX isn't mapped, because Discogs has no EX.
- `DISCOGS_SETTINGS["ALLOW_OFFERS"]` (default on) sets the form's start.
- The two new permissions (`can_from_unit`, `can_make_for_sale`) go to Admin
  on the next start; give them to any other role that needs them.
- The Discogs account's currency is assumed to be USD.

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
assumptions in the docstring (Pacific calendar month, a renewal each month on the start's Pacific day and time, with
`EbayListing.renews_at` (the listings sync sets it from eBay's `TimeLeft`) winning when it falls in the month, ended
listings counted up to `end_time`). Change them there and in
`tests/test_insertions.py`. Pacific time is worked out by hand there, on
purpose: it must not change with the `TIMEZONE` setting.

## Times

fbc stores naive UTC. `fbc/localtime.py` turns that into the `TIMEZONE`
setting's zone (`America/Chicago` by default) for display, in one format:
`2026-11-13 5:45 PM`, or `2026-11-13` for a date. Use it for anything new that
shows a time:

- templates: `{{ value | localtime }}` and `{{ value | localdate }}`;
- a view's DateTime columns: `formatters_columns = localtime.formatters("col")`;
- a form field on a UTC column: `fbc.widgets.LocalDateTimeField`, which shows
  local time and saves UTC;
- code: `localtime.fmt_datetime` and `localtime.fmt_date`.

Don't use it for a `Date` column the owner types. It also sets the log
timestamps. The image has `tzdata` (`apk add`, in `docker/Dockerfile`), as
Alpine has none; tests call `localtime.set_zone("America/Chicago")` so they
don't depend on the machine's zone.

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
