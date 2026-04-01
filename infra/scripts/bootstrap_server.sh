#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/opt/bobo"

sudo apt update && sudo apt upgrade -y

if ! command -v docker >/dev/null 2>&1; then
  if sudo apt install -y docker.io docker-compose-v2; then
    true
  else
    curl -fsSL https://get.docker.com | sudo sh
  fi
fi

sudo systemctl enable --now docker
sudo usermod -aG docker "$USER" || true
sudo apt install -y git curl

if [ ! -d "$PROJECT_DIR/.git" ]; then
  sudo mkdir -p "$PROJECT_DIR"
  sudo chown -R "$USER":"$USER" "$PROJECT_DIR"
  echo "Please clone your repo into $PROJECT_DIR before continuing."
  exit 0
fi

cd "$PROJECT_DIR"

docker compose -f infra/docker-compose.yml up -d postgres qdrant redis
docker compose -f infra/docker-compose.yml ps

echo "Bootstrap done."
