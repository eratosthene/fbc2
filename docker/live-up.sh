#!/bin/bash
# Build the image and start the app on the live server, from this checkout.
# See "Running on the live server" in README.md.
#
#   docker/live-up.sh [-n | --no-build]
#
# It runs docker/build.sh, then `sudo docker compose -f docker/compose.yml
# up -d --force-recreate`, which replaces the running container. The
# container brings the database up to date itself as it starts. -n skips the
# build and starts the image already there, which is all a change to
# config.py or docker/.env needs; it refuses an image built from other code
# than this checkout's. It makes an empty docker/.env if there isn't one.
set -euo pipefail

cd "$(dirname "$0")/.."

compose_file=docker/compose.yml
image=fbc2:latest
build=true
# What goes into the image, apart from config.py and docker/.env.
image_inputs=(fbc migrations docker/Dockerfile docker/docker-entrypoint.sh Pipfile Pipfile.lock)

usage() {
    sed -n '2,12s/^# \{0,1\}//p' "$0"
}

while [ $# -gt 0 ]; do
    case "$1" in
        -n | --no-build) build=false; shift ;;
        -h | --help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

# The container mounts config.py rather than having it built in.
if [ ! -f config.py ]; then
    echo "config.py is missing; copy config.py.tpl to config.py and fill it in." >&2
    exit 1
fi
# docker/compose.yml needs docker/.env, even an empty one. It's for secrets,
# so only its owner can read it.
if [ ! -f docker/.env ]; then
    (umask 077 && touch docker/.env)
    echo "Made an empty docker/.env (see \"Settings from the environment\" in README.md)."
fi

if [ "$build" = true ]; then
    docker/build.sh
else
    # An image from older code may not read docker/.env at all, and would
    # start with config.py alone. docker/build.sh labels the commit it built.
    revision=$(sudo docker image inspect --format \
        '{{ index .Config.Labels "fbc2.revision" }}' "$image" 2>/dev/null || true)
    if [ -z "$revision" ] || [ "$revision" = "<no value>" ] ||
        ! git diff --quiet "$revision" -- "${image_inputs[@]}" 2>/dev/null; then
        echo "The $image image wasn't built from this checkout's code." >&2
        echo "Run docker/live-up.sh without -n to rebuild it." >&2
        exit 1
    fi
fi

# Recreated even when nothing in the compose file changed, since `up -d`
# alone doesn't notice an edit to the mounted config.py.
sudo docker compose -f "$compose_file" up -d --force-recreate
cat <<MSG

Started; the app is on port 20000 once it's up.
  Follow the logs: docker/live-logs.sh
  Stop it:         docker/live-down.sh
MSG
