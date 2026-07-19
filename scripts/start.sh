#!/usr/bin/env bash

set -euo pipefail

IMAGE="${IMAGE:-ghcr.io/lakshaykamat/roxy:latest}"
CONTAINER_NAME="${CONTAINER_NAME:-roxy}"
DATA_VOLUME="${DATA_VOLUME:-roxy_data}"

if [[ ! -f .env ]]; then
    echo "Missing .env. Copy .env.example to .env and configure it before starting Roxy." >&2
    exit 1
fi

docker pull "$IMAGE"
docker volume create "$DATA_VOLUME" >/dev/null
docker rm --force "$CONTAINER_NAME" >/dev/null 2>&1 || true

docker run --detach \
    --name "$CONTAINER_NAME" \
    --env-file .env \
    --env DATABASE_PATH=/app/data/roxy.db \
    --publish 8000:8000 \
    --volume "$DATA_VOLUME:/app/data" \
    --init \
    --restart unless-stopped \
    "$IMAGE"
