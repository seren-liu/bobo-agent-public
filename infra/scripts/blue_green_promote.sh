#!/usr/bin/env bash
set -euo pipefail

SERVER_ALIAS="${1:-tx-server}"
REMOTE_ROOT="${DEPLOY_REMOTE_ROOT:-/opt/bobo}"
SSH_OPTS=(-o ServerAliveInterval=30 -o ServerAliveCountMax=20 -o StrictHostKeyChecking=accept-new)

ssh "${SSH_OPTS[@]}" "$SERVER_ALIAS" "
  set -euo pipefail
  cd '$REMOTE_ROOT'
  test -f '.deploy/active_slot'
  test -f '.deploy/canary_slot'

  ACTIVE_SLOT=\$(cat .deploy/active_slot)
  CANARY_SLOT=\$(cat .deploy/canary_slot)

  if [ -z \"\$CANARY_SLOT\" ]; then
    echo 'no canary slot to promote' >&2
    exit 1
  fi

  echo \"\$ACTIVE_SLOT\" > .deploy/previous_slot
  echo \"\$CANARY_SLOT\" > .deploy/active_slot
  : > .deploy/canary_slot

  bash infra/scripts/render_bobo_upstreams.sh \"\$CANARY_SLOT\" '' infra/nginx/runtime/bobo-upstreams.conf
  docker compose -f infra/docker-compose.yml exec -T nginx nginx -s reload

  echo 'promoted active_slot='\"\$(cat .deploy/active_slot)\"
  echo 'previous_slot='\"\$(cat .deploy/previous_slot)\"
"

echo "[blue-green-promote] done"
