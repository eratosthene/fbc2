#!/bin/bash
# Start the dev compose setup: the app from this checkout's code, against its
# own MariaDB. See "Testing with Docker" in README.rst.
#
#   dev-scripts/compose-up.sh [-d | --background]
#
# It runs in the foreground with the logs attached, so Ctrl-C stops it.
# -d starts it in the background instead, and says how to follow the logs
# and stop it.
set -euo pipefail

cd "$(dirname "$0")/.."

compose_file=docker-compose.dev.yml
url=http://127.0.0.1:5555
background=false

usage() {
    sed -n '2,9s/^# \{0,1\}//p' "$0"
}

while [ $# -gt 0 ]; do
    case "$1" in
        -d | --background) background=true; shift ;;
        -h | --help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

see_readme='See "Testing with Docker" in README.rst.'

if [ ! -f config.py ]; then
    echo "config.py is missing; copy config.py.tpl to config.py and fill it in." >&2
    echo "$see_readme" >&2
    exit 1
fi

# Compose also reads it from the environment, so only .env without it fails.
if [ -z "${FBC_DEV_DB_PASSWORD:-}" ] &&
    ! grep -qs '^FBC_DEV_DB_PASSWORD=.' .env; then
    echo "FBC_DEV_DB_PASSWORD isn't set in .env. To make one:" >&2
    echo "  echo \"FBC_DEV_DB_PASSWORD=\$(openssl rand -hex 16)\" >> .env" >&2
    echo "$see_readme" >&2
    exit 1
fi

if [ "$background" = false ]; then
    exec docker compose -f "$compose_file" up --build
fi

docker compose -f "$compose_file" up --build --detach
cat <<MSG

Started in the background; open $url once the app is up.
  Follow the logs: docker compose -f $compose_file logs -f
  Stop it:         docker compose -f $compose_file stop
MSG
