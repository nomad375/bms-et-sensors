#!/usr/bin/env bash
set -euo pipefail

echo "Cleaning Docker artifacts for THIS project only..."

COMPOSE_ARGS=(-f docker-compose.yml -f docker-compose.override.yml)

echo ">> Stopping and removing project containers/networks/volumes/images"
docker compose "${COMPOSE_ARGS[@]}" down --remove-orphans --volumes --rmi local || true

echo ">> Removing dangling layers only"
docker image prune -f >/dev/null 2>&1 || true

echo "Project Docker cleanup complete."
