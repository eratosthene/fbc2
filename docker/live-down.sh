#!/bin/bash
# Stop the app on the live server and remove its container. See "Running on
# the live server" in docs/production.md.
#
#   docker/live-down.sh
#
# It runs `sudo docker compose -f docker/compose.yml down`, and takes no
# other options, so it never removes volumes. docker/live-up.sh starts it
# again.
set -euo pipefail

cd "$(dirname "$0")/.."

compose_file=docker/compose.yml

usage() {
    sed -n '2,9s/^# \{0,1\}//p' "$0"
}

while [ $# -gt 0 ]; do
    case "$1" in
        -h | --help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

exec sudo docker compose -f "$compose_file" down
