#!/bin/bash
set -e

echo ">>> Starting local build and container refresh (MSCL)..."

# 1. Clean up old local image to prevent using stale cache
echo ">>> Removing old image..."
docker rmi nomad375/mscl-daq:latest 2>/dev/null || true

# 2. Build the image locally using the override file (Target service: mscl-app)
echo ">>> Compiling new image..."
docker compose -f docker-compose.yml -f docker-compose.override.yml build --no-cache mscl-app

# 3. Remove dangling build layers
echo ">>> Cleaning up system..."
docker image prune -f

# 4. Restart the specific service (Target service: mscl-app)
echo ">>> Restarting mscl-app (keeping data history)..."
docker compose up -d --no-deps mscl-app

echo ">>> Local build successfully applied."