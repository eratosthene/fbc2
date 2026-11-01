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

compose_file=docker/compose.dev.yml
env_file=docker/.env
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

# Compose reads .env from the compose file's folder, and also reads the
# password from the environment, so only docker/.env without it fails.
if [ -z "${FBC_DEV_DB_PASSWORD:-}" ] &&
    ! grep -qs '^FBC_DEV_DB_PASSWORD=.' "$env_file"; then
    if grep -qs '^FBC_DEV_DB_PASSWORD=.' .env; then
        echo "FBC_DEV_DB_PASSWORD is in .env, but Compose now reads $env_file. Move it:" >&2
        echo "  mv .env $env_file" >&2
    else
        echo "FBC_DEV_DB_PASSWORD isn't set in $env_file. To make one:" >&2
        echo "  echo \"FBC_DEV_DB_PASSWORD=\$(openssl rand -hex 16)\" >> $env_file" >&2
    fi
    echo "$see_readme" >&2
    exit 1
fi

# Dumps are only loaded into an empty volume, so this is just a warning.
if [ -d dev-db ] && [ ! -e docker/dev-db ]; then
    echo "Warning: dumps in dev-db/ aren't loaded any more; move them with:" >&2
    echo "  mv dev-db docker/dev-db" >&2
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
