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

WWW_DOMAIN="www.${DOMAIN}"
WEBROOT="/var/www/certbot"
NGINX_DIR="/opt/bobo/infra/nginx"
BOOTSTRAP_CONF="${NGINX_DIR}/nginx.bootstrap.conf"
FINAL_CONF="${NGINX_DIR}/nginx.conf"

sudo mkdir -p "$WEBROOT"
sudo chmod 755 "$WEBROOT"

if [ -f "$BOOTSTRAP_CONF" ]; then
  cp "$FINAL_CONF" "$FINAL_CONF.pre-cert"
  cp "$BOOTSTRAP_CONF" "$FINAL_CONF"
fi

docker compose -f /opt/bobo/infra/docker-compose.yml up -d nginx

sudo certbot certonly \
  --webroot \
  -w "$WEBROOT" \
  -d "$DOMAIN" \
  -d "$WWW_DOMAIN" \
  --email "$EMAIL" \
  --agree-tos \
  --no-eff-email \
  --non-interactive

if [ -f "${FINAL_CONF}.pre-cert" ]; then
  cp "${FINAL_CONF}.pre-cert" "$FINAL_CONF"
  rm -f "${FINAL_CONF}.pre-cert"
fi

docker compose -f /opt/bobo/infra/docker-compose.yml up -d --force-recreate nginx

sudo tee /etc/cron.d/bobo-certbot >/dev/null <<CRON
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
17 3 * * * root certbot renew --quiet --deploy-hook "docker compose -f /opt/bobo/infra/docker-compose.yml exec -T nginx nginx -s reload"
CRON

echo "Certificate issued for $DOMAIN and $WWW_DOMAIN"
