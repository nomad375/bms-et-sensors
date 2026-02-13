#!/usr/bin/env bash
set -euo pipefail

echo "Cleaning Docker artifacts for THIS project only..."

COMPOSE_ARGS=(-f docker-compose.yml -f docker-compose.override.yml)

echo ">> Stopping and removing project containers/networks/volumes"
docker compose "${COMPOSE_ARGS[@]}" down --remove-orphans --volumes || true

echo ">> Removing project images (if present)"
docker image rm \
  nomad375/mscl-daq:latest \
  nomad375/redlab-daq:latest \
  >/dev/null 2>&1 || true

echo ">> Removing dangling layers only"
docker image prune -f >/dev/null 2>&1 || true

echo "Project Docker cleanup complete."
