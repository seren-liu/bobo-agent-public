#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/opt/bobo"

cd "$PROJECT_DIR"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Please edit /opt/bobo/.env and rerun this script."
  exit 1
fi

docker compose -f infra/docker-compose.yml up -d

echo "=== compose status ==="
docker compose -f infra/docker-compose.yml ps

echo "=== backend health ==="
curl -fsS http://127.0.0.1/bobo/health && echo

if [ -f scripts/seed_menu.py ]; then
  echo "=== seed qdrant menu vectors ==="
  docker compose -f infra/docker-compose.yml exec -T backend python scripts/seed_menu.py || true
fi

echo "Part 9 first deploy done."
