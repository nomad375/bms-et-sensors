#!/usr/bin/env bash
set -euo pipefail

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.override.yml)

echo ">>> Restarting mscl-app (dev mode, bind-mounted code)..."
"${COMPOSE[@]}" up -d --no-deps mscl-app

echo ">>> Done."
