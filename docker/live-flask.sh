#!/bin/bash
# Run a `flask` command in the app's container on the live server. See
# "Running on the live server" in docs/production.md.
#
#   docker/live-flask.sh COMMAND [ARGS...]
#
# For example, `docker/live-flask.sh set-listing-skus --item 123 --apply`
# runs `sudo docker compose -f docker/compose.yml exec fbc flask ...`, and
# works with `> file` to save its output.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ $# -eq 0 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    sed -n '2,10s/^# \{0,1\}//p' "$0"
    exit 0
fi

# Without a terminal on stdout (output redirected to a file or a pipe), skip
# the pseudo-terminal: it would mix stderr into stdout and add carriage returns.
tty=()
if [ ! -t 1 ]; then
    tty=(-T)
fi

exec sudo docker compose -f docker/compose.yml exec "${tty[@]}" fbc flask "$@"
