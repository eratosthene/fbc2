# Running fbc in production

How to set up the app and run it on the live server. For working on the app
itself, see [Developing fbc](development.md).

## Setting up

Everything the app needs, apart from its code, is in `config.py`, or in
the environment
([Settings from the environment](#settings-from-the-environment)). It isn't in git, since it holds the live
credentials. Copy `config.py.tpl` to start one, and fill in:

- `SECRET_KEY`: a long random string, such as the output of
  `openssl rand -hex 32`.
- `SQLALCHEMY_DATABASE_URI`: the MariaDB database.
- `EBAY_SETTINGS` and `DISCOGS_SETTINGS`: the eBay and Discogs app keys,
  and the listing defaults. The comments there say where each one comes from.
  Once the app is running, Sales > Connect eBay and Discogs > Connect Discogs
  sign in to each site, and keep the tokens in the database.
- `MILEAGE_RATE`: the IRS standard mileage rate for the year, for expenses
  entered as miles. Without it, each one needs its rate typed in.

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

To run a `flask` command in the live container without typing the Docker
part, use `docker/live-flask.sh`, such as
`docker/live-flask.sh set-listing-skus --apply`.

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

## Setting SKUs on old listings

Listings made from eBay Drafts carry `fbc-<unit id>` as their SKU (Custom
label). Listings from before that don't. `set-listing-skus` sets it on every
active listing linked to exactly one unsold Unit. It's a one-off, run in the
live container. It turns `FBC_SKIP_VIEWS` on itself, so don't set it.

Run it in this order. The first only shows what it would do, and writes
`skipped-listing-skus.csv` (in the container, so add `--csv /tmp/skipped.csv`
and `docker cp` it out if you want to keep it):

```bash
sudo docker compose -f docker/compose.yml exec fbc flask set-listing-skus
```

Then change one listing, with an item ID from the dry run:

```bash
sudo docker compose -f docker/compose.yml exec fbc flask set-listing-skus --item 123456789012 --apply
```

Check that listing in Seller Hub before going on:

- Its Custom label is `fbc-<unit id>`. eBay's docs don't say outright that a
  SKU can be added to a listing this way.
- The revise cost nothing and used no free insertion, and the listing kept
  its shipping details, title, price and pictures. The command sends only the
  item ID and SKU, but eBay's docs warn that a revise can drop shipping
  details that aren't sent again.

Then the rest:

```bash
sudo docker compose -f docker/compose.yml exec fbc flask set-listing-skus --apply
```

It leaves a listing alone, and lists it in the CSV with the reason, when it
has no Unit linked, several Units or only sold ones, a SKU typed on eBay
(`--overwrite` replaces that), or an `fbc-` SKU naming another Unit. Link
those by hand and run it again: listings already right are skipped.
`--limit N` changes at most N listings. It waits half a second between
changes, and a listing eBay refuses is reported and the run goes on. It
refuses in dev mode.

## Settings from the environment

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

### Moving secrets out of config.py

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
