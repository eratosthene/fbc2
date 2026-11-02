#!/bin/sh
set -e

# config.py isn't in the image; docker/compose.yml mounts it. Without this
# check the first sign would be an ImportError from deep in the app.
if [ ! -f /app/config.py ]; then
    echo "There's no /app/config.py: mount one (see docker/compose.yml)." >&2
    exit 1
fi

# Once, before gunicorn starts its workers: build a new database from the
# models, or bring an existing one up to date with its migrations.
flask setup-db

exec "$@"
