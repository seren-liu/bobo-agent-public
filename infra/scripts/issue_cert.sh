#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"

if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
  echo "Usage: $0 <domain> <email>"
  exit 1
fi

sudo apt update
sudo apt install -y certbot

# 若 nginx 占用 80，先停
if docker ps --format '{{.Names}}' | grep -q '^infra-nginx-1$'; then
  docker compose -f /opt/bobo/infra/docker-compose.yml stop nginx
fi

sudo certbot certonly --standalone -d "$DOMAIN" --email "$EMAIL" --agree-tos --no-eff-email

docker compose -f /opt/bobo/infra/docker-compose.yml up -d nginx

echo "Certificate issued for $DOMAIN"
