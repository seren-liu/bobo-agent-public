#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVER_ALIAS="${1:-tx-server}"
IMAGE_TAG="${2:-$(git -C "$ROOT_DIR" rev-parse --short HEAD)}"
REMOTE_ROOT="${DEPLOY_REMOTE_ROOT:-/opt/bobo}"
TMP_TAR="/tmp/bobo-backend-${IMAGE_TAG}.tar"
REMOTE_TAR="/tmp/bobo-backend-${IMAGE_TAG}.tar"
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"
SSH_OPTS=(-o ServerAliveInterval=30 -o ServerAliveCountMax=20 -o StrictHostKeyChecking=accept-new)
KEEP_LOCAL_TAR="${KEEP_LOCAL_TAR:-1}"

bash "$ROOT_DIR/infra/scripts/sync_source_to_remote.sh" "$SERVER_ALIAS"

echo "[deploy-tar] build backend image -> bobo-backend:${IMAGE_TAG}"
docker buildx build \
  --platform "$TARGET_PLATFORM" \
  --load \
  -t "bobo-backend:${IMAGE_TAG}" \
  -t "bobo-backend:latest" \
  "$ROOT_DIR/backend"

echo "[deploy-tar] export backend image -> $TMP_TAR"
docker save "bobo-backend:latest" -o "$TMP_TAR"

echo "[deploy-tar] upload image tar with resume support -> $SERVER_ALIAS:$REMOTE_TAR"
rsync --partial --append --progress \
  -e "ssh ${SSH_OPTS[*]}" \
  "$TMP_TAR" "$SERVER_ALIAS:$REMOTE_TAR"

TARGET_SLOT="${TARGET_SLOT:-}"
if [[ -z "$TARGET_SLOT" ]]; then
  CURRENT_ACTIVE="$(ssh "${SSH_OPTS[@]}" "$SERVER_ALIAS" "test -f '$REMOTE_ROOT/.deploy/active_slot' && cat '$REMOTE_ROOT/.deploy/active_slot' || true" 2>/dev/null || true)"
  if [[ "$CURRENT_ACTIVE" == "blue" ]]; then
    TARGET_SLOT="green"
  else
    TARGET_SLOT="blue"
  fi
fi

if [[ "$TARGET_SLOT" != "blue" && "$TARGET_SLOT" != "green" ]]; then
  echo "TARGET_SLOT must be blue or green" >&2
  exit 1
fi

TARGET_PORT=18001
if [[ "$TARGET_SLOT" == "green" ]]; then
  TARGET_PORT=18002
fi

echo "[deploy-tar] remote load + deploy slot $TARGET_SLOT"
ssh "${SSH_OPTS[@]}" "$SERVER_ALIAS" "
  set -euo pipefail
  docker load -i '$REMOTE_TAR'
  rm -f '$REMOTE_TAR'
  cd '$REMOTE_ROOT'
  mkdir -p .deploy
  docker tag 'bobo-backend:latest' 'bobo-backend:$TARGET_SLOT'
  docker compose -f infra/docker-compose.yml up -d backend_$TARGET_SLOT nginx
  for i in \$(seq 1 45); do
    if curl -fsS 'http://127.0.0.1:$TARGET_PORT/bobo/health' >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
  curl -fsS 'http://127.0.0.1:$TARGET_PORT/bobo/health' >/dev/null
  if [ ! -f .deploy/active_slot ]; then
    echo '$TARGET_SLOT' > .deploy/active_slot
    : > .deploy/previous_slot
    : > .deploy/canary_slot
  else
    echo '$TARGET_SLOT' > .deploy/canary_slot
  fi
  ACTIVE_SLOT=\$(cat .deploy/active_slot)
  CANARY_SLOT=\$(cat .deploy/canary_slot 2>/dev/null || true)
  bash infra/scripts/render_bobo_upstreams.sh \"\$ACTIVE_SLOT\" \"\$CANARY_SLOT\" infra/nginx/runtime/bobo-upstreams.conf
  docker compose -f infra/docker-compose.yml exec -T nginx nginx -s reload
  docker compose -f infra/docker-compose.yml ps
"

if [[ "$KEEP_LOCAL_TAR" == "0" ]]; then
  rm -f "$TMP_TAR"
fi
echo "[deploy-tar] done"
