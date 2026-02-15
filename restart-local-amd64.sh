#!/usr/bin/env bash
set -euo pipefail

echo ">>> Rebuild and restart stack..."

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.override.yml)

echo ">>> Building app images for amd64..."
"${COMPOSE[@]}" build --no-cache mscl-app redlab-app

echo ">>> Restarting full stack services..."
"${COMPOSE[@]}" up -d \
  influxdb \
  grafana \
  dashboard \
  mosquitto \
  mqtt-explorer \
  zigbee2mqtt \
  telegraf \
  mscl-app \
  redlab-app

echo ">>> Stack refreshed on amd64."
