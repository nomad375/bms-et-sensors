#!/bin/bash
echo "Setting up RedLab DAQ Project..."
if [ ! -f .env ]; then
    echo "Creating .env from template..."
    cp .env.example .env
    echo "PLEASE EDIT .env FILE WITH YOUR SECRETS!"
fi
docker compose down -v
docker compose up -d --build
docker compose logs -f
