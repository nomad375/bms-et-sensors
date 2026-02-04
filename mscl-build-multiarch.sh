#!/bin/bash
# Exit immediately if a command exits with a non-zero status
set -e

IMAGE_NAME="nomad375/mscl-daq:latest"
BUILDER_NAME="daq-multi-builder"

echo ">>> Preparing Multi-Arch Builder..."

# 1. Create a new buildx builder if it doesn't exist
# We use the docker-container driver to support multi-platform builds
if ! docker buildx inspect $BUILDER_NAME > /dev/null 2>&1; then
    echo ">>> Creating new builder: $BUILDER_NAME..."
    docker buildx create --name $BUILDER_NAME --driver docker-container --use
fi

# 2. Activate the builder
docker buildx use $BUILDER_NAME
docker buildx inspect --bootstrap

echo ">>> Starting FORCED multi-platform build and push ($IMAGE_NAME)..."

# 3. Build for both AMD64 and ARM64
# --no-cache: Forces a full rebuild of all layers, ignoring local cache
# --pull: Always attempts to pull a newer version of the base image (python:3.12-slim)
# --push: Automatically uploads the final manifest to Docker Hub
docker buildx build \
    --platform linux/amd64,linux/arm64 \
    -f Dockerfile.mscl \
    -t $IMAGE_NAME \
    --no-cache \
    --pull \
    --push .

echo ">>> [SUCCESS] Fresh multi-arch image has been pushed to Docker Hub."