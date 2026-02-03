#!/bin/bash

if ! groups | grep -q docker; then
    echo "Current user is not in the 'docker' group."
    echo "Running with sudo..."
    sudo docker compose up -d --build
else
    docker compose up -d --build
fi

echo "Setting up RedLab DAQ Project..."
if [ ! -f .env ]; then
    echo "Creating .env from template..."
    cp .env.example .env
    echo "PLEASE EDIT .env FILE WITH YOUR SECRETS!"
fi
docker compose down -v
docker compose up -d --build
docker compose logs -f
