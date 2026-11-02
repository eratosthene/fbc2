#!/bin/bash
# Build the image and start the app on the live server, from this checkout.
# See "Running on the live server" in README.md.
#
#   docker/live-up.sh [-n | --no-build]
#
# It runs docker/build.sh, then `sudo docker compose -f docker/compose.yml
# up -d`, which replaces the running container. The container brings the
# database up to date itself as it starts. -n skips the build and starts the
# image already there.
set -euo pipefail

cd "$(dirname "$0")/.."

compose_file=docker/compose.yml
build=true

usage() {
    sed -n '2,10s/^# \{0,1\}//p' "$0"
}

while [ $# -gt 0 ]; do
    case "$1" in
        -n | --no-build) build=false; shift ;;
        -h | --help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ "$build" = true ]; then
    docker/build.sh
fi

sudo docker compose -f "$compose_file" up -d
cat <<MSG

Started; the app is on port 20000 once it's up.
  Follow the logs: docker/live-logs.sh
  Stop it:         docker/live-down.sh
MSG
