#!/usr/bin/env bash
set -euo pipefail

echo "==========================================="
echo " Raspberry Pi DAQ Stack Setup Utility"
echo "==========================================="

PROJECT_DIR="${PROJECT_DIR:-$HOME/redlab-daq-project}"
REPO_URL="${REPO_URL:-https://github.com/nomad375/redlab-daq-project.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"

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
  echo ">>> Docker installed. Re-login may be required for docker group."
}

ensure_compose_plugin() {
  if docker compose version >/dev/null 2>&1; then
    echo ">>> Docker Compose plugin is available."
    return
  fi

  echo ">>> Installing docker-compose-plugin..."
  sudo apt-get install -y docker-compose-plugin
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
  chmod +x ./*.sh

  echo ">>> Building local ARM-compatible app images..."
  ./redlab-build-local.sh
  ./mscl-build-local.sh

  echo ">>> Starting full stack..."
  docker compose up -d
}

show_post_checks() {
  echo ">>> Stack status:"
  docker compose ps
  echo ">>> Tail logs examples:"
  echo "    ./logs.sh mscl-stream"
  echo "    ./logs.sh mscl-app"
  echo "    ./logs.sh redlab-app"
}

install_prerequisites
install_docker_if_needed
ensure_compose_plugin
prepare_repo
prepare_env_file
build_and_start_stack
show_post_checks

echo "==========================================="
echo " Setup finished."
echo "==========================================="
