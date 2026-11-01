#!/bin/bash
# Run fbc on this machine with Flask's reloading dev server, from the
# Pipenv environment (`pipenv install --dev`). See "Development" in
# README.rst; for the app and a MariaDB in Docker, use compose-up.sh.
#
#   dev-scripts/run-local.sh [-H HOST] [-p PORT]
#
# It listens on 127.0.0.1:5556 unless told otherwise, by the options or by
# FBC_HOST and FBC_PORT. 5556 so it can run alongside compose-up.sh's app,
# which has 5555. Give -H a LAN address (or 0.0.0.0) to reach it from other
# machines.
set -euo pipefail

cd "$(dirname "$0")/.."

host="${FBC_HOST:-127.0.0.1}"
port="${FBC_PORT:-5556}"

usage() {
    sed -n '2,11s/^# \{0,1\}//p' "$0"
}

while [ $# -gt 0 ]; do
    case "$1" in
        -H | --host) host="${2:?$1 needs a host}"; shift 2 ;;
        -p | --port) port="${2:?$1 needs a port}"; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ ! -f config.py ]; then
    echo "config.py is missing; copy config.py.tpl to config.py and fill it in." >&2
    exit 1
fi

if ! command -v pipenv >/dev/null 2>&1; then
    echo "pipenv is not installed; see \"Development\" in README.rst." >&2
    exit 1
fi

exec pipenv run flask --app fbc run --debug --host "$host" --port "$port"
