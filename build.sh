#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

# Build with BuildKit via the buildx plugin; the legacy builder is deprecated.
if ! sudo docker buildx version >/dev/null 2>&1; then
    echo "The docker buildx plugin is not installed. Install it, then re-run this script:" >&2
    echo "  Debian/Ubuntu (Docker's apt repo):  sudo apt install docker-buildx-plugin" >&2
    echo "  Debian/Ubuntu (distro docker.io):   sudo apt install docker-buildx" >&2
    echo "  Other platforms:                    https://docs.docker.com/go/buildx/" >&2
    exit 1
fi

if [ ! -f config.py ]; then
    echo "config.py is missing; copy config.py.tpl to config.py and fill it in." >&2
    exit 1
fi

sudo docker buildx build --load -t fbc2:latest .
