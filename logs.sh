#!/bin/bash

# Usage:
# ./logs.sh             -> shows logs for all containers
# ./logs.sh influxdb    -> shows logs for InfluxDB only

if [ -z "$1" ]; then
    echo ">>> Following logs for ALL containers (Ctrl+C to exit)..."
    docker compose logs -f
else
    echo ">>> Following logs for: $1 (Ctrl+C to exit)..."
    docker compose logs -f "$1"
fi
