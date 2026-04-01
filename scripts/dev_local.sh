#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_COMPOSE="$ROOT_DIR/infra/docker-compose.yml"
LOCAL_COMPOSE="$ROOT_DIR/infra/docker-compose.local.yml"
LOCAL_ENV_EXAMPLE="$ROOT_DIR/.env.local.example"
ENV_EXAMPLE="$ROOT_DIR/.env.example"
LOCAL_ENV="$ROOT_DIR/.env.local"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/dev_local.sh init [--force]  # create .env.local for host-based local dev
  bash scripts/dev_local.sh up              # start postgres/redis/qdrant for local dev
  bash scripts/dev_local.sh down            # stop local dev stack
  bash scripts/dev_local.sh ps              # show stack status
  bash scripts/dev_local.sh backend         # run backend locally on host (uvicorn --reload)
  bash scripts/dev_local.sh test            # run backend tests locally
EOF
}

compose() {
  if [[ -f "$LOCAL_ENV" ]]; then
    docker compose --env-file "$LOCAL_ENV" -f "$BASE_COMPOSE" -f "$LOCAL_COMPOSE" "$@"
  else
    docker compose -f "$BASE_COMPOSE" -f "$LOCAL_COMPOSE" "$@"
  fi
}

require_cmd() {
  local cmd=$1
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[dev-local] Missing required command: $cmd"
    exit 1
  fi
}

prepare_backend_env() {
  cd "$ROOT_DIR/backend"
  if [[ ! -d .venv ]]; then
    python3 -m venv .venv
  fi

  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -q --upgrade pip
  pip install -q -e ".[all]"

  if [[ ! -f "$LOCAL_ENV" ]]; then
    echo "[dev-local] Missing .env.local. Run: bash scripts/dev_local.sh init"
    exit 1
  fi

  set -a
  # shellcheck disable=SC1090
  source "$LOCAL_ENV"
  set +a
}

run_init() {
  local force="${2:-}"

  local template_file=""
  if [[ -f "$LOCAL_ENV_EXAMPLE" ]]; then
    template_file="$LOCAL_ENV_EXAMPLE"
  elif [[ -f "$ENV_EXAMPLE" ]]; then
    template_file="$ENV_EXAMPLE"
  else
    echo "[dev-local] Missing template file: $LOCAL_ENV_EXAMPLE or $ENV_EXAMPLE"
    exit 1
  fi

  if [[ -f "$LOCAL_ENV" && "$force" != "--force" ]]; then
    echo "[dev-local] $LOCAL_ENV already exists. Re-run with --force to overwrite."
    exit 1
  fi

  cp "$template_file" "$LOCAL_ENV"

  perl -0pi -e 's#^DATABASE_URL=.*$#DATABASE_URL=postgresql://bobo:bobo123\@127.0.0.1:15432/bobo#m' "$LOCAL_ENV"
  perl -0pi -e 's#^REDIS_URL=.*$#REDIS_URL=redis://127.0.0.1:16379/0#m' "$LOCAL_ENV"
  perl -0pi -e 's#^QDRANT_URL=.*$#QDRANT_URL=http://127.0.0.1:16333#m' "$LOCAL_ENV"
  perl -0pi -e 's#^CORS_PROD_ORIGIN=.*$#CORS_PROD_ORIGIN=http://localhost:8081#m' "$LOCAL_ENV"

  {
    echo ""
    echo "LOCAL_POSTGRES_PORT=15432"
    echo "LOCAL_REDIS_PORT=16379"
    echo "LOCAL_QDRANT_HTTP_PORT=16333"
    echo "LOCAL_QDRANT_GRPC_PORT=16334"
  } >> "$LOCAL_ENV"

  echo "[dev-local] Created $LOCAL_ENV from $(basename "$template_file")"
  echo "[dev-local] Fill in API keys if you want vision / agent / embedding features."
}

run_up() {
  require_cmd docker
  compose up -d postgres redis qdrant
  compose ps
}

run_down() {
  require_cmd docker
  compose down
}

run_ps() {
  require_cmd docker
  compose ps
}

run_backend() {
  require_cmd python3
  prepare_backend_env
  echo "[dev-local] backend -> http://127.0.0.1:${BACKEND_PORT:-8000}"
  uvicorn app.main:app --host 0.0.0.0 --port "${BACKEND_PORT:-8000}" --reload
}

run_test() {
  require_cmd python3
  prepare_backend_env
  PYTHONPATH="$ROOT_DIR:$ROOT_DIR/backend${PYTHONPATH:+:$PYTHONPATH}" pytest
}

cmd="${1:-}"
case "$cmd" in
  init) run_init "$@" ;;
  up) run_up ;;
  down) run_down ;;
  ps) run_ps ;;
  backend) run_backend ;;
  test) run_test ;;
  *) usage; exit 1 ;;
esac
