#!/usr/bin/env bash
set -euo pipefail

echo ">>> Local rebuild (current arch) for app services..."

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.override.yml"

echo ">>> Removing old images (mscl/redlab)..."
docker rmi nomad375/mscl-daq:latest nomad375/redlab-daq:latest 2>/dev/null || true

echo ">>> Building images for this host architecture..."
$COMPOSE build --no-cache mscl-app redlab-app

echo ">>> Cleaning dangling layers..."
docker image prune -f >/dev/null 2>&1 || true

echo ">>> Restarting services with fresh images..."
$COMPOSE up -d --no-deps mscl-app redlab-app

echo ">>> Done."

