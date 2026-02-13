#!/usr/bin/env bash
set -euo pipefail

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.override.yml)
BUILD_ARGS=()

if [[ "${NO_CACHE:-0}" == "1" ]]; then
  BUILD_ARGS+=(--no-cache)
fi

echo ">>> Building mscl-app image for current host architecture..."
"${COMPOSE[@]}" build "${BUILD_ARGS[@]}" mscl-app

echo ">>> Restarting mscl-app..."
"${COMPOSE[@]}" up -d --no-deps mscl-app

echo ">>> Done (mscl-app)."
