#!/usr/bin/env bash

set -euo pipefail

IMAGE="${IMAGE:-ghcr.io/lakshaykamat/roxy:latest}"
CONTAINER_NAME="${CONTAINER_NAME:-roxy}"
PROJECT_DIRECTORY="$(pwd)"
DATA_DIRECTORY="${DATA_DIRECTORY:-$PROJECT_DIRECTORY/data}"

if [[ ! -f .env ]]; then
    echo "Missing .env. Copy .env.example to .env and configure it before starting Roxy." >&2
    exit 1
fi

docker rm --force "$CONTAINER_NAME" >/dev/null 2>&1 || true
docker pull "$IMAGE"
CONTAINER_UID="$(docker run --rm --entrypoint id "$IMAGE" -u)"
CONTAINER_GID="$(docker run --rm --entrypoint id "$IMAGE" -g)"
mkdir -p "$DATA_DIRECTORY"
chown "$CONTAINER_UID:$CONTAINER_GID" "$DATA_DIRECTORY"

docker run --detach \
    --name "$CONTAINER_NAME" \
    --env-file .env \
    --env DATABASE_PATH=/app/data/roxy.db \
    --publish 8888:8888 \
    --volume "$DATA_DIRECTORY:/app/data" \
    --init \
    --restart unless-stopped \
    "$IMAGE"

docker image prune --force >/dev/null
