# fbc

A Flask-AppBuilder app on MariaDB that keeps a record shop's stock and sales
in step with Discogs and eBay. One person runs it and works on it; most
changes start from a GitHub issue and arrive as a PR from a `claude/` branch
in its own worktree. `README.md` and `docs/` are the user-facing guide; this
file is what they don't say.

## Commands

- Tests: `pipenv run pytest -n auto` (parallel, about 30 s; add `-q`, or drop
  `-n auto` and give a path to run one test).
- Format and lint: Ruff for Python (`ruff-format`, then `ruff --fix`) and
  mdformat for Markdown, both through pre-commit, which pins them; neither is
  in the Pipfile. Commits run those and the file checks. The test suite runs
  before each push, not each commit, so run it yourself before committing.
- New migration: `pipenv run flask --app fbc db migrate -m "..."`, then read
  it over: autogenerate misses data changes and renames.
- Run the app: `dev-scripts/run-local.sh` (127.0.0.1:5556, needs
  `config.py`), or `dev-scripts/compose-up.sh` for the app on :5555 with its
  own MariaDB (`docker/compose.dev.yml`).

## Layout

- `fbc/__init__.py` builds the app, the database and every view and menu
  entry **at import time**, from the top-level `config` module.
- `fbc/models/`: one module per area (inventory, discogs, ebay, sales,
  supplies). `fbc/views/` matches it.
- `fbc/listing.py` (eBay drafts and listings), `fbc/shipping.py` (Ship This),
  `fbc/discogs.py`, `fbc/sync.py` (syncs run in a thread, their state in
  files, since gunicorn workers don't share memory), `fbc/devmode.py`.
- `migrations/`: Alembic, through Flask-Migrate. `flask setup-db` builds a new
  database from the models and stamps it, or migrates an existing one.
- `scripts/`: one-off and admin scripts. `scripts/deprecated_*` is history;
  leave it alone. `dev-scripts/`: local-only helpers and the fake-data
  scripts.

## Things that aren't obvious

- `tests/conftest.py` puts a fake `config` module (in-memory SQLite) in
  `sys.modules` before `fbc` is imported. Tests never read `config.py`.
- SQLite passing proves little about the schema. Live is MariaDB 10.11 with
  `utf8mb3` tables, and MySQL refuses foreign keys between columns whose types
  or character sets differ (`tests/test_models_schema.py` checks the types).
  The Migrations workflow runs new migrations against MariaDB and checks the
  result matches the models with `flask db check`.
- Dev mode (`DEV_MODE` in `config.py` or `FBC_DEV_MODE=1`) blanks the eBay and
  Discogs keys and refuses every request to their hosts. Anything new that
  reaches eBay or Discogs has to go through the guarded functions in
  `fbc/devmode.py`'s docstring, or check `devmode` itself.
- Custom form fields and list widgets live in `fbc/widgets.py`. Menu entries
  for a filtered list ("Units w/out Listings") are links built in
  `fbc/__init__.py` from filter dicts the views export, not cloned views.

## Safety

- `config.py` holds the live database, eBay and Discogs credentials and isn't
  in git; `config.py.tpl` is its template, and any new setting goes there with
  a comment. Don't read `config.py`, `docker/.env` or `docker/dev-db/`.
- Never touch the live server, the live database or the live eBay and Discogs
  accounts. Run the app here with `FBC_DEV_MODE=1`.
- `docker/compose.dev.yml`'s `fbc2-dev` project holds a copy of live data:
  never `down -v` it, never commit or paste anything from it. For a throwaway
  database, use another project name (`-p fbc2-something`).
- A worktree has no `config.py`. To run the app from one, symlink the main
  checkout's: `ln -s "$(git rev-parse --git-common-dir)/../config.py" .`

## Writing

- Docs, comments, UI text and PR descriptions are plain English, short
  sentences, in the app's own names: Units, Purchase Lots, Storage Boxes,
  eBay Drafts, Ship This, Sales Receipts. Comments say why, not what.
- When behaviour or setup changes, update the doc it belongs in, in the same
  PR: `docs/using.md` for what the app does, `docs/development.md` for
  working on it, `docs/production.md` for the live server. `README.md` is an
  overview with links; change it only when that changes, such as a new area
  of the app. Docs are Markdown, with commands in fenced `bash` blocks.
- PR descriptions follow this shape: `Closes #N.`, a one-line summary, then
  sections as needed: How it works, Differences from the issue, and Testing,
  which says what was run, against what, and what *wasn't* run. CI writes its
  results into the description, so don't add them by hand.
