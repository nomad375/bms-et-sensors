#!/usr/bin/env bash
set -euo pipefail

# Usage:
# ./logs.sh             -> shows logs for all containers
# ./logs.sh influxdb    -> shows logs for InfluxDB only

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.override.yml)

if [[ "${1:-}" == "" ]]; then
    echo ">>> Following logs for ALL containers (Ctrl+C to exit)..."
    "${COMPOSE[@]}" logs -f
else
    echo ">>> Following logs for: $1 (Ctrl+C to exit)..."
    "${COMPOSE[@]}" logs -f "$1"
fi
