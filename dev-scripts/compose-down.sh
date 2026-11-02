#!/bin/bash
# Stop the dev compose setup and remove its containers, keeping its database.
# See "Testing with Docker" in README.md.
#
#   dev-scripts/compose-down.sh [-p | --project NAME]
#
# The database is a copy of live data, so this never removes its volume, and
# passes nothing on to `docker compose down`. dev-scripts/compose-up.sh starts
# it again with the same data. -p acts on a throwaway project instead, such as
# fbc2-test.
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck source=dev-scripts/_compose.sh
. dev-scripts/_compose.sh

usage() {
    sed -n '2,10s/^# \{0,1\}//p' "$0"
}

while [ $# -gt 0 ]; do
    case "$1" in
        -p | --project) project=${2:?-p needs a project name}; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

check_project
check_local_docker

compose down
