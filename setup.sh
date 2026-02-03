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

echo ">>> Checking for new image versions on Docker Hub..."
docker compose pull daq-collector

echo ">>> Starting services..."
docker compose up -d

# Очистка старых версий образов для экономии места
docker image prune -f

echo ">>> System is running."
docker compose logs -f --tail=50