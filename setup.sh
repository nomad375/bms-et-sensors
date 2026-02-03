#!/bin/bash

# 1. Permission check (Docker group)
if ! groups | grep -q docker; then
    DOCKER_CMD="sudo docker"
    echo "Running with sudo permissions..."
else
    DOCKER_CMD="docker"
fi

echo "--- RedLab DAQ System Manager ---"

# 2. .env file priority check
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        echo ">>> Creating .env from template..."
        cp .env.example .env
        echo "!!! ACTION REQUIRED: Edit .env file with your secrets, then run this script again."
        exit 0
    else
        echo "!!! Error: .env.example not found. Cannot proceed."
        exit 1
    fi
fi

# 3. Check for updates on GitHub
echo ">>> Checking for updates in the repository..."
git fetch origin main

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" != "$REMOTE" ]; then
    echo ">>> New version detected on GitHub! Updating code..."
    git pull origin main
    NEEDS_REBUILD=true
else
    echo ">>> Your local code is already up to date."
    NEEDS_REBUILD=false
fi

# 4. Decide whether to rebuild/restart
# We rebuild if there was an update OR if the containers are not running
if [ "$NEEDS_REBUILD" = true ] || [ -z "$($DOCKER_CMD compose ps -q)" ]; then
    echo ">>> Starting/Rebuilding containers..."
    $DOCKER_CMD compose down
    $DOCKER_CMD image prune -f
    $DOCKER_CMD compose up -d --build
    echo ">>> System is running and updated."
else
    echo ">>> Containers are already running. No restart needed."
fi

# 5. Show logs
echo "--- Following logs (Press Ctrl+C to stop) ---"
$DOCKER_CMD compose logs -f --tail=50