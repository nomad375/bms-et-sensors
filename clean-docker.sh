#!/usr/bin/env bash
set -euo pipefail

echo "Cleaning all docker artifacts..."

containers=$(docker ps -a -q)
if [ -n "$containers" ]; then
  echo ">> Removing containers"
  docker rm -f $containers >/dev/null 2>&1 || true
fi

images=$(docker images -q)
if [ -n "$images" ]; then
  echo ">> Removing images"
  docker rmi -f $images >/dev/null 2>&1 || true
fi

echo ">> Pruning volumes"
docker volume prune -f >/dev/null 2>&1 || true

echo ">> Pruning networks"
docker network prune -f >/dev/null 2>&1 || true

echo "Docker cleanup complete."
