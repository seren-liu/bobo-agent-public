#!/usr/bin/env bash
set -euo pipefail

SERVER_ALIAS="${1:-tx-server}"

if [ ! -d web/dist ]; then
  echo "web/dist not found. Run web build first: cd web && npm run build"
  exit 1
fi

rsync -az --delete web/dist/ "$SERVER_ALIAS":/opt/bobo/web/dist/
ssh "$SERVER_ALIAS" 'docker compose -f /opt/bobo/infra/docker-compose.yml restart nginx'

echo "web/dist synced and nginx restarted."
