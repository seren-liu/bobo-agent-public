#!/usr/bin/env bash
set -euo pipefail

SERVER_ALIAS="${1:-tx-server}"
REMOTE_ROOT="${DEPLOY_REMOTE_ROOT:-/opt/bobo}"
SSH_OPTS=(-o ServerAliveInterval=30 -o ServerAliveCountMax=20 -o StrictHostKeyChecking=accept-new)

ssh "${SSH_OPTS[@]}" "$SERVER_ALIAS" "
  set -euo pipefail
  cd '$REMOTE_ROOT'
  echo active_slot=\$(cat .deploy/active_slot 2>/dev/null || echo none)
  echo canary_slot=\$(cat .deploy/canary_slot 2>/dev/null || echo none)
  echo previous_slot=\$(cat .deploy/previous_slot 2>/dev/null || echo none)
  docker compose -f infra/docker-compose.yml ps
"
