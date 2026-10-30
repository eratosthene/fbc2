#!/bin/sh
set -e

# Bring the database up to date once, before gunicorn starts its workers.
flask db upgrade

exec "$@"
