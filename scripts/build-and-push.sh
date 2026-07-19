#!/usr/bin/env bash

set -euo pipefail

IMAGE="${IMAGE:-ghcr.io/lakshaykamat/roxy:latest}"
PLATFORM="${PLATFORM:-linux/amd64}"

docker buildx build \
    --platform "$PLATFORM" \
    --tag "$IMAGE" \
    --push \
    .
