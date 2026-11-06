#!/bin/sh
# Check an image's packages are installed and importable, and that config.py,
# which holds the live credentials, isn't in it. The app (and the entrypoint's
# `flask setup-db`) needs a database, so this skips the entrypoint.
#
#   .github/scripts/image-smoke.sh IMAGE
set -eu

docker run --rm --entrypoint python "${1:?Usage: image-smoke.sh IMAGE}" -c \
    "import os, flask_appbuilder, flask_migrate, gunicorn, pymysql, discogs_client, ebaysdk, PIL, dotenv;
assert not os.path.exists('/app/config.py'), 'config.py is in the image'"
