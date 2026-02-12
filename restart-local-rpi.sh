#!/usr/bin/env bash
set -euo pipefail

echo ">>> Rebuild and restart stack on Raspberry Pi (includes rpi-ap)..."

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.rpi.yml"

echo ">>> Building app + AP images for Pi..."
$COMPOSE build --no-cache mscl-app redlab-app rpi-ap

echo ">>> Restarting services..."
$COMPOSE up -d --no-deps mscl-app redlab-app rpi-ap influxdb grafana

echo ">>> Stack refreshed on Raspberry Pi."

