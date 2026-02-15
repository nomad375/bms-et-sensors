#!/usr/bin/env bash
set -euo pipefail

echo "==========================================="
echo " Raspberry Pi DAQ Stack Setup Utility"
echo "==========================================="

PROJECT_DIR="${PROJECT_DIR:-$HOME/bms-et-sensors}"
REPO_URL="${REPO_URL:-https://github.com/nomad375/redlab-daq-project.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
DOCKER_CMD=(docker)
COMPOSE_CMD=(docker compose)

install_prerequisites() {
  echo ">>> Installing base packages..."
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl git
}

install_docker_if_needed() {
  if command -v docker >/dev/null 2>&1; then
    echo ">>> Docker is already installed."
    return
  fi

  echo ">>> Installing Docker..."
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER"
  sudo systemctl enable --now docker
  echo ">>> Docker installed. Re-login may be required for docker group."
}

resolve_docker_access() {
  if docker info >/dev/null 2>&1; then
    DOCKER_CMD=(docker)
  else
    DOCKER_CMD=(sudo docker)
  fi
  COMPOSE_CMD=("${DOCKER_CMD[@]}" compose)
}

ensure_compose_plugin() {
  if "${COMPOSE_CMD[@]}" version >/dev/null 2>&1; then
    echo ">>> Docker Compose plugin is available."
    return
  fi

  echo ">>> Installing docker-compose-plugin..."
  sudo apt-get install -y docker-compose-plugin
}

disable_modemmanager_if_present() {
  if ! systemctl list-unit-files 2>/dev/null | grep -q "^ModemManager.service"; then
    echo ">>> ModemManager service not found. Skipping."
    return
  fi

  echo ">>> Disabling ModemManager (prevents USB serial capture on DAQ dongles)..."
  sudo systemctl stop ModemManager.service || true
  sudo systemctl disable ModemManager.service || true
  sudo systemctl mask ModemManager.service || true
}

prepare_repo() {
  if [ ! -d "$PROJECT_DIR/.git" ]; then
    echo ">>> Cloning repository into $PROJECT_DIR"
    git clone "$REPO_URL" "$PROJECT_DIR"
  fi

  cd "$PROJECT_DIR"
  echo ">>> Updating repository ($REPO_BRANCH)..."
  git fetch --all --tags --prune
  git checkout "$REPO_BRANCH"
  git pull --ff-only origin "$REPO_BRANCH"
}

prepare_env_file() {
  if [ -f ".env" ]; then
    echo ">>> .env already exists."
    return
  fi

  if [ -f ".env.example" ]; then
    cp .env.example .env
    echo "!!! .env created from .env.example"
    echo "!!! Edit .env before production use:"
    echo "    nano .env"
  else
    touch .env
    echo "!!! Empty .env created. Fill required variables before use."
  fi
}

build_and_start_stack() {
  echo ">>> Building local ARM-compatible app images..."
  "${COMPOSE_CMD[@]}" -f docker-compose.yml -f docker-compose.override.yml build --no-cache redlab-app mscl-app

  echo ">>> Starting full stack..."
  "${COMPOSE_CMD[@]}" -f docker-compose.yml -f docker-compose.override.yml up -d
}

show_post_checks() {
  echo ">>> Stack status:"
  "${COMPOSE_CMD[@]}" ps
  echo ">>> Tail logs examples:"
  echo "    ./logs.sh mscl-stream"
  echo "    ./logs.sh mscl-app"
  echo "    ./logs.sh redlab-app"
}

install_prerequisites
install_docker_if_needed
resolve_docker_access
ensure_compose_plugin
disable_modemmanager_if_present
prepare_repo
prepare_env_file
build_and_start_stack
show_post_checks

echo "==========================================="
echo " Setup finished."
echo "==========================================="
