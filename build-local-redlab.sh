#!/usr/bin/env bash
set -euo pipefail

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.override.yml)
BUILD_ARGS=()

if [[ "${NO_CACHE:-0}" == "1" ]]; then
  BUILD_ARGS+=(--no-cache)
fi

echo ">>> Building redlab-app image for current host architecture..."
"${COMPOSE[@]}" build "${BUILD_ARGS[@]}" redlab-app

echo ">>> Restarting redlab-app..."
"${COMPOSE[@]}" up -d --no-deps redlab-app

echo ">>> Done (redlab-app)."
