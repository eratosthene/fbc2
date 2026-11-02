#!/bin/bash
# Empty the dev compose setup's database and load the dumps in docker/dev-db/
# into it, such as a new copy of live data. See "A copy of live data" in
# docs/development.md.
#
#   dev-scripts/compose-reset-db.sh [-y | --yes] [-p | --project NAME]
#
# It drops and recreates the database inside the dev MariaDB, starting it if
# need be, and keeps the volume. With no dumps the database is left empty, for
# the app to build. It deletes everything in the database, so it asks first;
# -y skips that. -p acts on a throwaway project instead, such as fbc2-test.
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck source=dev-scripts/_compose.sh
. dev-scripts/_compose.sh

dump_dir=docker/dev-db
yes=false

usage() {
    sed -n '2,11s/^# \{0,1\}//p' "$0"
}

while [ $# -gt 0 ]; do
    case "$1" in
        -y | --yes) yes=true; shift ;;
        -p | --project) project=${2:?-p needs a project name}; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

check_project
check_local_docker

if [ -z "$(docker volume ls -q --filter "label=com.docker.compose.project=$project" \
    --filter label=com.docker.compose.volume=db)" ]; then
    echo "$project has no database yet. dev-scripts/compose-up.sh makes one, and"
    echo "loads the dumps in $dump_dir/ into it itself."
    exit 0
fi

# The same files, in the same order, that MariaDB loads into a new volume.
dumps=()
for f in "$dump_dir"/*; do
    case "$f" in
        *.sql | *.sql.gz) dumps+=("$f") ;;
    esac
done

echo "This deletes everything in $project's database, and any changes made in it."
if [ ${#dumps[@]} -eq 0 ]; then
    echo "There are no dumps in $dump_dir/, so it's left empty."
else
    echo "Then it loads, in this order:"
    printf '  %s\n' "${dumps[@]}"
fi

if [ "$yes" = false ]; then
    if [ ! -t 0 ]; then
        echo "Not asking without a terminal; pass -y to go ahead." >&2
        exit 1
    fi
    read -r -p "Type yes to go ahead: " answer
    if [ "$answer" != yes ]; then
        echo "Stopped; nothing changed."
        exit 1
    fi
fi

# Runs mariadb in the db container as the app's user, which owns its
# database, with the password from the container's own environment rather
# than docker/.env.
db_client() {
    # shellcheck disable=SC2016 # Expanded in the container.
    compose exec -T db sh -c \
        'MYSQL_PWD=$MARIADB_PASSWORD exec mariadb -u"$MARIADB_USER" "$@"' sh "$@"
}

# The app holds connections, and would recreate tables as they're dropped.
app_was_running=false
if compose ps --status running --services | grep -qx app; then
    app_was_running=true
    compose stop app
fi

compose up --detach --wait db
database=$(compose exec -T db printenv MARIADB_DATABASE)

echo "Emptying the $database database"
db_client -e "DROP DATABASE IF EXISTS \`$database\`; CREATE DATABASE \`$database\`;"

for f in "${dumps[@]}"; do
    echo "Loading $f ($(du -h "$f" | cut -f1))"
    case "$f" in
        *.gz) gzip -dc "$f" | db_client "$database" ;;
        *) db_client "$database" <"$f" ;;
    esac
done

if [ "$app_was_running" = true ]; then
    # It runs `flask setup-db` as it starts, bringing the dump up to date.
    compose start app
    echo "Done; the app is starting again."
else
    echo "Done. dev-scripts/compose-up.sh starts the app, which brings it up to date."
fi
echo "The database has no users; make an admin to sign in with:"
echo "  docker compose -f $compose_file -p $project exec app flask fab create-admin"
