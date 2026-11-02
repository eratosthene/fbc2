#!/bin/bash
# Follow the logs of the app on the live server. See "Running on the live
# server" in docs/production.md.
#
#   docker/live-logs.sh [-n | --tail LINES]
#
# It runs `sudo docker compose -f docker/compose.yml logs -f`, starting from
# the last 100 lines, or LINES with -n ("all" for everything). Ctrl-C stops
# following; the app keeps running.
set -euo pipefail

cd "$(dirname "$0")/.."

compose_file=docker/compose.yml
tail=100

usage() {
    sed -n '2,9s/^# \{0,1\}//p' "$0"
}

while [ $# -gt 0 ]; do
    case "$1" in
        -n | --tail) tail=${2:?-n needs a number of lines}; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

exec sudo docker compose -f "$compose_file" logs --follow --tail "$tail"
