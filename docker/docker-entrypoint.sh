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

# The scheduler (see "Scheduled syncs" in docs/production.md) runs in this
# container, next to the web server, because the syncs' job lock is this
# container's. Only for the web server: `docker run ... flask ...` and the
# dev compose file's `flask run` don't start one. It exits 0 when it's off
# (SCHEDULER = False, or dev mode), which ends this loop; a crash restarts it.
if [ "$1" = "gunicorn" ]; then
    (until flask scheduler; do
        echo "The scheduler stopped; starting it again in 30 seconds." >&2
        sleep 30
    done) &
fi

exec "$@"
