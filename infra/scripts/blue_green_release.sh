#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

SERVER_ALIAS="${1:-tx-server}"
TARGET_SLOT="${2:-}"
IMAGE_TAG="${3:-$(git -C "$ROOT_DIR" rev-parse --short HEAD)}"
REMOTE_ROOT="${DEPLOY_REMOTE_ROOT:-/opt/bobo}"
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"
SSH_OPTS=(-o ServerAliveInterval=30 -o ServerAliveCountMax=20 -o StrictHostKeyChecking=accept-new)

: "${TCR_REGISTRY:?Missing TCR_REGISTRY}"
: "${TCR_NAMESPACE:?Missing TCR_NAMESPACE}"
: "${TCR_REPOSITORY:?Missing TCR_REPOSITORY}"
: "${TCR_USERNAME:?Missing TCR_USERNAME}"
: "${TCR_PASSWORD:?Missing TCR_PASSWORD}"

get_remote_active_slot() {
  ssh "${SSH_OPTS[@]}" "$SERVER_ALIAS" "test -f '$REMOTE_ROOT/.deploy/active_slot' && cat '$REMOTE_ROOT/.deploy/active_slot' || true" 2>/dev/null || true
}

if [[ -z "$TARGET_SLOT" ]]; then
  CURRENT_ACTIVE="$(get_remote_active_slot)"
  if [[ "$CURRENT_ACTIVE" == "blue" ]]; then
    TARGET_SLOT="green"
  else
    TARGET_SLOT="blue"
  fi
fi

if [[ "$TARGET_SLOT" != "blue" && "$TARGET_SLOT" != "green" ]]; then
  echo "target slot must be blue or green" >&2
  exit 1
fi

TARGET_PORT=18001
if [[ "$TARGET_SLOT" == "green" ]]; then
  TARGET_PORT=18002
fi

REMOTE_IMAGE="${TCR_REGISTRY}/${TCR_NAMESPACE}/${TCR_REPOSITORY}:${IMAGE_TAG}"
LATEST_IMAGE="${TCR_REGISTRY}/${TCR_NAMESPACE}/${TCR_REPOSITORY}:latest"

bash "$ROOT_DIR/infra/scripts/sync_source_to_remote.sh" "$SERVER_ALIAS"

echo "[blue-green-release] build backend image -> $REMOTE_IMAGE"
docker buildx build \
  --platform "$TARGET_PLATFORM" \
  --load \
  -t "$REMOTE_IMAGE" \
  -t "$LATEST_IMAGE" \
  "$ROOT_DIR/backend"

echo "[blue-green-release] docker login -> $TCR_REGISTRY"
printf '%s' "$TCR_PASSWORD" | docker login "$TCR_REGISTRY" -u "$TCR_USERNAME" --password-stdin

echo "[blue-green-release] push images"
docker push "$REMOTE_IMAGE"
docker push "$LATEST_IMAGE"

echo "[blue-green-release] deploy target slot -> $TARGET_SLOT"
ssh "${SSH_OPTS[@]}" "$SERVER_ALIAS" "
  set -euo pipefail
  mkdir -p '$REMOTE_ROOT/.deploy'
  cd '$REMOTE_ROOT'
  printf '%s' '$TCR_PASSWORD' | docker login '$TCR_REGISTRY' -u '$TCR_USERNAME' --password-stdin
  docker pull '$REMOTE_IMAGE'
  docker tag '$REMOTE_IMAGE' 'bobo-backend:$TARGET_SLOT'
  docker compose -f infra/docker-compose.yml up -d backend_$TARGET_SLOT nginx
  docker compose -f infra/docker-compose.yml exec -T backend_$TARGET_SLOT python -m alembic -c alembic.ini upgrade head
  for i in \$(seq 1 45); do
    if curl -fsS 'http://127.0.0.1:$TARGET_PORT/bobo/health' >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
  curl -fsS 'http://127.0.0.1:$TARGET_PORT/bobo/health' >/dev/null

  if [ ! -f '$REMOTE_ROOT/.deploy/active_slot' ]; then
    echo '$TARGET_SLOT' > '$REMOTE_ROOT/.deploy/active_slot'
    : > '$REMOTE_ROOT/.deploy/previous_slot'
    : > '$REMOTE_ROOT/.deploy/canary_slot'
  else
    echo '$TARGET_SLOT' > '$REMOTE_ROOT/.deploy/canary_slot'
  fi

  ACTIVE_SLOT=\$(cat '$REMOTE_ROOT/.deploy/active_slot')
  CANARY_SLOT=\$(cat '$REMOTE_ROOT/.deploy/canary_slot' 2>/dev/null || true)
  bash infra/scripts/render_bobo_upstreams.sh \"\$ACTIVE_SLOT\" \"\$CANARY_SLOT\" infra/nginx/runtime/bobo-upstreams.conf
  docker compose -f infra/docker-compose.yml exec -T nginx nginx -s reload

  echo 'active_slot='\"\$ACTIVE_SLOT\"
  echo 'canary_slot='\"\$CANARY_SLOT\"
  echo 'target_slot=$TARGET_SLOT ready on http://127.0.0.1:$TARGET_PORT/bobo/health'
"

echo "[blue-green-release] done"
echo "[blue-green-release] 灰度验证时带请求头: X-Bobo-Canary: 1"
