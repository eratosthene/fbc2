#!/bin/bash
# Run an image CI published on the live server, check it came up, and go back
# to the last one if it didn't. See docs/deploy.md.
#
#   docker/live-deploy.sh               show what runs now and what ran before
#   docker/live-deploy.sh TAG           pull and run TAG: sha-1a2b3c4, main
#   docker/live-deploy.sh --previous    run the image that ran before this one
#
# Options: --backed-up (you've backed up the database; needed when the image's
# migrations differ from the running one's), --timeout SECONDS (how long to
# wait for it to be healthy, default 300).
#
# The image is set as FBC_IMAGE in docker/.env, and the one before it is kept
# in docker/.previous-image. If the new image isn't healthy in time, this goes
# back to the previous one, unless a migration may have run: then the database
# may be ahead of the old code, so it stops and says what to do.
set -euo pipefail

cd "$(dirname "$0")/.."

registry_image=ghcr.io/eratosthene/fbc2
env_file=docker/.env
previous_file=docker/.previous-image
compose=(sudo docker compose -f docker/compose.yml)
timeout=300
backed_up=false
target=
use_previous=false

usage() {
    sed -n '2,16s/^# \{0,1\}//p' "$0"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --backed-up) backed_up=true; shift ;;
        --previous) use_previous=true; shift ;;
        --timeout) timeout=${2:?--timeout needs a number of seconds}; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        -*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
        *)
            [ -z "$target" ] || { echo "Give one tag." >&2; exit 2; }
            target=$1; shift ;;
    esac
done

if [ ! -f config.py ]; then
    echo "config.py is missing; copy config.py.tpl to config.py and fill it in." >&2
    exit 1
fi
if [ ! -f "$env_file" ]; then
    (umask 077 && touch "$env_file")
fi

current() {
    sed -n 's/^FBC_IMAGE=//p' "$env_file" | tail -n 1
}

# Replace FBC_IMAGE in docker/.env, and nothing else in it. Written to a new
# file so a failure part-way leaves the old one, and kept private since the
# file holds secrets.
set_image() {
    local tmp
    tmp=$(umask 077 && mktemp "$env_file.XXXXXX")
    { grep -v '^FBC_IMAGE=' "$env_file" || true; } > "$tmp"
    echo "FBC_IMAGE=$1" >> "$tmp"
    mv "$tmp" "$env_file"
}

# The migrations an image carries, one per line.
migrations_of() {
    sudo docker run --rm --entrypoint sh "$1" -c \
        'ls /app/migrations/versions | grep "\.py$" | sort'
}

# Wait for the container to be healthy: 0 when it is, 1 when it isn't in time.
wait_healthy() {
    local waited=0 cid state health poll=${FBC_DEPLOY_POLL:-5}
    while [ "$waited" -le "$timeout" ]; do
        cid=$("${compose[@]}" ps -q fbc 2>/dev/null || true)
        if [ -n "$cid" ]; then
            state=$(sudo docker inspect --format '{{.State.Status}}' "$cid" 2>/dev/null || true)
            health=$(sudo docker inspect --format '{{.State.Health.Status}}' "$cid" 2>/dev/null || true)
            [ "$health" = healthy ] && return 0
            [ "$state" = exited ] && return 1
        fi
        sleep "$poll"
        waited=$((waited + poll + 1))
    done
    return 1
}

now=$(current)
# What runs now, for comparing and going back to; an unpinned one is the image
# docker/build.sh made.
was=${now:-fbc2:latest}

if [ "$use_previous" = false ] && [ -z "$target" ]; then
    echo "Running:  $was$([ -n "$now" ] || echo " (built here, not pinned)")"
    echo "Before:   $(cat "$previous_file" 2>/dev/null || echo none)"
    echo
    usage
    exit 0
fi

if [ "$use_previous" = true ]; then
    [ -z "$target" ] || { echo "--previous takes no tag." >&2; exit 2; }
    new=$(cat "$previous_file" 2>/dev/null || true)
    [ -n "$new" ] || { echo "There's no previous image recorded." >&2; exit 1; }
else
    case "$target" in
        */*) new=$target ;;
        *) new=$registry_image:$target ;;
    esac
fi

echo "Pulling $new"
if ! sudo docker pull "$new"; then
    echo "Couldn't pull $new; nothing has changed." >&2
    exit 1
fi

# Migrations only go forward. If the two images carry different ones, the
# database is about to change (or is ahead of the new code), and going back
# afterwards isn't safe without a backup.
migrations=false
if [ "$new" != "$was" ]; then
    new_migrations=$(migrations_of "$new")
    old_migrations=$(migrations_of "$was" 2>/dev/null || echo unknown)
    if [ "$new_migrations" != "$old_migrations" ]; then
        migrations=true
    fi
fi
if [ "$migrations" = true ] && [ "$backed_up" = false ]; then
    cat >&2 <<MSG
$new has different migrations from the running image (or the running image
couldn't be read), so starting it may change the database, and the change
can't be undone by going back to the old image.

Back up the database first (see docs/deploy.md), then run this again with
--backed-up. Nothing has changed.
MSG
    exit 1
fi

if [ "$was" != "$new" ]; then
    echo "$was" > "$previous_file"
fi
set_image "$new"
"${compose[@]}" up -d --force-recreate

if wait_healthy; then
    echo "$(date -u +%FT%TZ) $new ok" >> docker/deploys.log
    echo "Running $new, and healthy."
    exit 0
fi

echo "$(date -u +%FT%TZ) $new failed" >> docker/deploys.log
echo >&2
echo "$new didn't become healthy within ${timeout}s. Its last logs:" >&2
"${compose[@]}" logs --tail 30 >&2 || true
echo >&2

if [ "$migrations" = true ]; then
    cat >&2 <<MSG
Not going back automatically: the image's migrations may have run, and the old
code may not work with the changed database. $new is still set, and the
previous image is $was.
Fix the cause and run this again, or restore the backup and run
docker/live-deploy.sh --previous --backed-up.
MSG
    exit 1
fi

echo "Going back to $was" >&2
set_image "$was"
"${compose[@]}" up -d --force-recreate
if wait_healthy; then
    echo "$(date -u +%FT%TZ) $was rolled-back" >> docker/deploys.log
    echo "Back on $was, and healthy. $new was not deployed." >&2
else
    echo "$(date -u +%FT%TZ) $was rollback-failed" >> docker/deploys.log
    echo "$was isn't healthy either. Check docker/live-logs.sh." >&2
fi
exit 1
