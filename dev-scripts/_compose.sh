# Shared by the dev-scripts/compose-*.sh scripts that stop or clear the dev
# compose setup; they source it from the repo root. Its database is a copy of
# live data, so they only ever act on a project of docker/compose.dev.yml, on
# this machine.

compose_file=docker/compose.dev.yml
project=fbc2-dev

# -p takes a throwaway project's name, such as fbc2-test. Only fbc2-<name>,
# since plain fbc2 is the live server's project.
check_project() {
    case "$project" in
        fbc2-?*) ;;
        *)
            echo "Refusing project \"$project\": only fbc2-dev or another fbc2-<name>." >&2
            exit 2
            ;;
    esac
}

# Compose follows DOCKER_HOST and the current docker context, either of which
# can point at another machine, such as the live server.
check_local_docker() {
    local host
    host=${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}')}
    case "$host" in
        unix://* | npipe://*) ;;
        *)
            echo "Refusing to run: Docker is at $host, not on this machine." >&2
            echo "Unset DOCKER_HOST, or switch with: docker context use default" >&2
            exit 1
            ;;
    esac
}

compose() {
    docker compose -f "$compose_file" -p "$project" "$@"
}
