#!/usr/bin/env bash
set -euo pipefail

BUILDER="daq-multi-builder"

echo ">>> Preparing buildx builder (${BUILDER})..."
if ! docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
  docker buildx create --name "$BUILDER" --driver docker-container --use
fi
docker buildx use "$BUILDER"
docker buildx inspect --bootstrap

echo ">>> Building and pushing multi-arch mscl-daq..."
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -f Dockerfile.mscl \
  -t nomad375/mscl-daq:latest \
  --pull --no-cache --push .

echo ">>> Building and pushing multi-arch redlab-daq..."
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -f Dockerfile.redlab \
  -t nomad375/redlab-daq:latest \
  --pull --no-cache --push .

echo ">>> Multi-arch push complete (mscl/redlab)."

