#!/usr/bin/env bash
set -euo pipefail

SERVER_ALIAS="${1:-tx-server}"
REMOTE_ROOT="${DEPLOY_REMOTE_ROOT:-/opt/bobo}"
SSH_OPTS=(-o ServerAliveInterval=30 -o ServerAliveCountMax=20 -o StrictHostKeyChecking=accept-new)

ssh "${SSH_OPTS[@]}" "$SERVER_ALIAS" "
  set -euo pipefail
  cd '$REMOTE_ROOT'
  test -f '.deploy/active_slot'
  test -f '.deploy/previous_slot'

  ACTIVE_SLOT=\$(cat .deploy/active_slot)
  PREVIOUS_SLOT=\$(cat .deploy/previous_slot)

  if [ -z \"\$PREVIOUS_SLOT\" ]; then
    echo 'no previous slot available for rollback' >&2
    exit 1
  fi

  echo \"\$ACTIVE_SLOT\" > .deploy/canary_slot
  echo \"\$PREVIOUS_SLOT\" > .deploy/active_slot
  : > .deploy/previous_slot

  bash infra/scripts/render_bobo_upstreams.sh \"\$PREVIOUS_SLOT\" \"\$ACTIVE_SLOT\" infra/nginx/runtime/bobo-upstreams.conf
  docker compose -f infra/docker-compose.yml exec -T nginx nginx -s reload

  echo 'rolled_back active_slot='\"\$(cat .deploy/active_slot)\"
  echo 'canary_slot='\"\$(cat .deploy/canary_slot)\"
"

echo "[blue-green-rollback] done"
