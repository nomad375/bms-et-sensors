#!/bin/bash

# Exit on any error
set -e

echo "--- Starting Raspberry Pi Deployment for RedLab DAQ ---"

# 1. Update System
echo ">>> Updating system packages..."
sudo apt-get update && sudo apt-get upgrade -y

# 2. Install Docker
if ! [ -x "$(command -v docker)" ]; then
    echo ">>> Installing Docker..."
    curl -sSL https://get.docker.com | sh
    # Add current user to docker group
    sudo usermod -aG docker $USER
    echo "!!! Docker installed. Note: Group changes will apply after logout/login."
else
    echo ">>> Docker is already installed."
fi

# 3. Install Docker Compose dependencies
echo ">>> Installing Docker Compose and Git..."
sudo apt-get install -y git python3-pip libffi-dev python3-dev make build-essential
sudo usermod -aG docker $USER
newgrp docker

# 4. Set up UDEV rules for RedLab-TC
echo ">>> Configuring UDEV rules for USB-TC (Vendor ID: 09db)..."
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="09db", MODE="0666"' | sudo tee /etc/udev/rules.d/99-redlab.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
echo ">>> UDEV rules applied."

# 5. Prepare Project Directory (Optional, if running from project root)
if [ ! -f .env.example ]; then
    echo "--- Notice: .env.example not found in current folder. ---"
else
    if [ ! -f .env ]; then
        echo ">>> Creating .env file from template..."
        cp .env.example .env
        echo "!!! IMPORTANT: Edit .env file with your secrets before running docker compose."
    fi
fi

echo "--- Setup Complete! ---"
echo "Recommended next steps:"
echo "1. Log out and log back in (to apply Docker group permissions)."
echo "2. Edit your .env file."
echo "3. Run: docker compose up -d --build"