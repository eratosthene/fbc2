#!/bin/sh
set -e

# Once, before gunicorn starts its workers: build a new database from the
# models, or bring an existing one up to date with its migrations.
flask setup-db

exec "$@"
