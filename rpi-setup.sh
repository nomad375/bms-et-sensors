#!/bin/bash
# Выход при любой ошибке
set -e

echo "==========================================="
echo "   Raspberry Pi DAQ Stack Setup Utility    "
echo "==========================================="

# 1. Обновление системы
echo ">>> Updating system packages..."
sudo apt-get update && sudo apt-get upgrade -y

# 2. Установка Docker
if ! command -v docker &> /dev/null; then
    echo ">>> Installing Docker..."
    curl -sSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
    echo ">>> Docker installed. User added to 'docker' group."
    # Применяем права группы сразу для текущей сессии
    sudo chmod 666 /var/run/docker.sock
else
    echo ">>> Docker is already installed."
fi

# 3. Установка git
if ! command -v git &> /dev/null; then
    echo ">>> Installing git..."
    sudo apt-get install -y git
fi

# 4. Клонирование репозитория
PROJECT_DIR="redlab-daq-project"
REPO_URL="https://github.com/nomad375/redlab-daq-project.git"

if [ ! -d "$PROJECT_DIR" ]; then
    echo ">>> Cloning repository: $REPO_URL"
    git clone "$REPO_URL" "$PROJECT_DIR"
else
    echo ">>> Project directory already exists. Skipping clone."
fi

cd "$PROJECT_DIR"

# 5. Настройка переменных окружения (.env)
if [ ! -f ".env" ]; then
    echo ">>> .env file not found. Creating from example..."
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "!!! IMPORTANT: Please edit the .env file with your InfluxDB tokens now."
        echo ">>> Command: nano .env"
    else
        echo "!!! WARNING: .env.example not found. Creating empty .env..."
        touch .env
    fi
fi

# 6. Права на исполнение скриптов
chmod +x *.sh

# 7. Финальный запуск
echo ">>> Initializing the stack..."
# Используем sudo, если группа еще не подхватилась в текущей сессии
if docker ps >/dev/null 2>&1; then
    ./setup.sh
else
    sudo ./setup.sh
fi

echo "==========================================="
echo "   Setup finished! Check logs: ./logs.sh   "
echo "==========================================="