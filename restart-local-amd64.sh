#!/usr/bin/env bash
set -euo pipefail

echo ">>> Rebuild and restart stack..."

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.override.yml)

echo ">>> Building app images for amd64..."
"${COMPOSE[@]}" build --no-cache mscl-app redlab-app

echo ">>> Restarting services..."
"${COMPOSE[@]}" up -d --no-deps mscl-app redlab-app influxdb grafana

echo ">>> Stack refreshed on amd64."
