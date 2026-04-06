#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MOBILE_DIR="$ROOT_DIR/mobile"
BASE_COMPOSE="$ROOT_DIR/infra/docker-compose.yml"
LOCAL_COMPOSE="$ROOT_DIR/infra/docker-compose.local.yml"
LOCAL_ENV="$ROOT_DIR/.env.local"
BACKEND_PORT="${BACKEND_PORT:-8000}"
BACKEND_LOG="$ROOT_DIR/.backend.mobile.log"
BACKEND_PID=""
STARTED_BACKEND="false"

cleanup() {
  if [[ "$STARTED_BACKEND" == "true" && -n "$BACKEND_PID" ]]; then
    echo ""
    echo "[run-mobile] 正在停止脚本拉起的后端..."
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
    pkill -f "uvicorn app.main:app.*--port $BACKEND_PORT" 2>/dev/null || true
  fi
}
trap cleanup EXIT SIGINT SIGTERM

usage() {
  cat <<'EOF'
Usage:
  bash scripts/run_mobile_local.sh simulator [--no-clear]
  bash scripts/run_mobile_local.sh device [YOUR_MAC_LAN_IP] [--no-clear]
  bash scripts/run_mobile_local.sh logs

Examples:
  bash scripts/run_mobile_local.sh simulator
  bash scripts/run_mobile_local.sh simulator --no-clear
  bash scripts/run_mobile_local.sh device <your-mac-lan-ip>
  bash scripts/run_mobile_local.sh logs
EOF
}

compose() {
  if [[ -f "$LOCAL_ENV" ]]; then
    docker compose --env-file "$LOCAL_ENV" -f "$BASE_COMPOSE" -f "$LOCAL_COMPOSE" "$@"
  else
    docker compose -f "$BASE_COMPOSE" -f "$LOCAL_COMPOSE" "$@"
  fi
}

is_host_port_open() {
  local port=$1
  python3 - <<PY >/dev/null 2>&1
import socket
sock = socket.socket()
sock.settimeout(0.5)
try:
    sock.connect(("127.0.0.1", int("$port")))
except OSError:
    raise SystemExit(1)
else:
    raise SystemExit(0)
finally:
    sock.close()
PY
}

detect_ip() {
  local ip=""
  ip="$(ipconfig getifaddr en0 2>/dev/null || true)"
  if [[ -z "$ip" ]]; then
    ip="$(ipconfig getifaddr en1 2>/dev/null || true)"
  fi
  echo "$ip"
}

print_service_status() {
  local svc=$1
  if compose ps --status running "$svc" 2>/dev/null | grep -q "$svc"; then
    echo "[run-mobile] ✔ $svc 已运行"
  else
    echo "[run-mobile] ⚠ $svc 未运行"
  fi
}

ensure_base_services() {
  local need_start="false"
  local svc
  for svc in postgres redis qdrant; do
    if ! compose ps --status running "$svc" 2>/dev/null | grep -q "$svc"; then
      need_start="true"
      break
    fi
  done

  if [[ "$need_start" == "false" ]]; then
    if ! is_host_port_open "${LOCAL_POSTGRES_PORT:-15432}" \
      || ! is_host_port_open "${LOCAL_REDIS_PORT:-16379}" \
      || ! is_host_port_open "${LOCAL_QDRANT_HTTP_PORT:-16333}"; then
      need_start="true"
    fi
  fi

  if [[ "$need_start" == "true" ]]; then
    echo "[run-mobile] 正在启动或重建本地基础服务..."
    compose up -d --force-recreate postgres redis qdrant
  fi
}

start_backend() {
  if [[ ! -f "$LOCAL_ENV" ]]; then
    echo "[run-mobile] ✘ 缺少 .env.local，请先运行: bash scripts/dev_local.sh init"
    exit 1
  fi

  : > "$BACKEND_LOG"

  cd "$ROOT_DIR/backend"
  if [[ ! -d .venv ]]; then
    echo "[run-mobile] 创建 Python 虚拟环境..."
    python3 -m venv .venv
  fi

  # shellcheck disable=SC1091
  source .venv/bin/activate
  if ! python -m pip --version >/dev/null 2>&1; then
    python -m ensurepip --upgrade
  fi
  python -m pip install -q --upgrade pip
  python -m pip install -q -e ".[all]"

  set -a
  # shellcheck disable=SC1090
  source "$LOCAL_ENV"
  set +a

  echo "[run-mobile] 正在后台启动后端..."
  uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload >> "$BACKEND_LOG" 2>&1 &
  BACKEND_PID=$!
  STARTED_BACKEND="true"
  cd "$ROOT_DIR"
}

wait_for_backend() {
  local url="http://127.0.0.1:${BACKEND_PORT}/bobo/health"
  local elapsed=0
  local max_wait=30

  while ! curl -sf "$url" >/dev/null 2>&1; do
    sleep 1
    elapsed=$((elapsed + 1))
    if [[ $elapsed -ge $max_wait ]]; then
      echo "[run-mobile] ✘ 后端启动超时: $url"
      echo "[run-mobile]   查看日志: tail -50 $BACKEND_LOG"
      exit 1
    fi
  done

  echo "[run-mobile] ✔ 后端已就绪: $url"
}

check_backend_health() {
  local url="http://127.0.0.1:${BACKEND_PORT}/bobo/health"
  if curl -sf "$url" >/dev/null 2>&1; then
    echo "[run-mobile] ✔ 后端健康检查通过: $url"
    return 0
  fi

  echo "[run-mobile] ✘ 后端未就绪: $url"
  echo "[run-mobile]   先运行:"
  echo "[run-mobile]   1. bash scripts/dev_local.sh up"
  echo "[run-mobile]   2. bash scripts/dev_local.sh backend"
  return 1
}

mode="${1:-}"
if [[ -z "$mode" ]]; then
  usage
  exit 1
fi

if [[ "$mode" == "logs" ]]; then
  if [[ ! -f "$BACKEND_LOG" ]]; then
    echo "[run-mobile] 后端日志不存在: $BACKEND_LOG"
    echo "[run-mobile] 先运行: bash scripts/run_mobile_local.sh simulator"
    echo "[run-mobile] 或者: bash scripts/run_mobile_local.sh device"
    exit 1
  fi
  echo "[run-mobile] 正在查看后端日志: $BACKEND_LOG"
  tail -f "$BACKEND_LOG"
  exit 0
fi

clear_flag="--clear"
if [[ "${*: -1}" == "--no-clear" ]]; then
  clear_flag=""
fi

if [[ ! -d "$MOBILE_DIR" ]]; then
  echo "[run-mobile] mobile directory not found: $MOBILE_DIR"
  exit 1
fi

case "$mode" in
  simulator)
    api_url="http://127.0.0.1:${BACKEND_PORT}"
    host_mode="localhost"
    connection_hint="iOS Simulator"
    ;;
  device)
    ip="${2:-$(detect_ip)}"
    if [[ -z "$ip" ]]; then
      echo "[run-mobile] Could not detect your Mac LAN IP. Pass it manually:"
      echo "  bash scripts/run_mobile_local.sh device 192.168.x.x"
      exit 1
    fi
    api_url="http://$ip:${BACKEND_PORT}"
    host_mode="lan"
    connection_hint="Expo Go on same Wi-Fi"
    ;;
  *)
    usage
    exit 1
    ;;
esac

echo "╔══════════════════════════════════════╗"
echo "║     Bobo 本地移动端启动脚本         ║"
echo "╚══════════════════════════════════════╝"
echo ""

echo "[run-mobile] 步骤 1/4：检查基础服务..."
ensure_base_services
print_service_status postgres
print_service_status redis
print_service_status qdrant

echo ""
echo "[run-mobile] 步骤 2/4：检查后端..."
if ! check_backend_health; then
  start_backend
  wait_for_backend
fi

echo ""
echo "[run-mobile] 步骤 3/4：准备 Expo..."
cd "$MOBILE_DIR"
if [[ ! -d node_modules ]]; then
  echo "[run-mobile] node_modules 缺失，正在安装依赖..."
  npm install
else
  echo "[run-mobile] ✔ 移动端依赖已存在"
fi

# Stop stale Expo servers for this project to avoid cache/port confusion.
pkill -f "expo start.*$MOBILE_DIR" >/dev/null 2>&1 || true

export EXPO_PUBLIC_API_URL="$api_url"

echo ""
echo "[run-mobile] 步骤 4/4：启动 Expo..."
echo ""
echo "┌─────────────────────────────────────────────────┐"
echo "│  模式: $mode"
echo "│  Expo host: $host_mode"
echo "│  API: $EXPO_PUBLIC_API_URL"
if [[ "$mode" == "device" ]]; then
echo "│  Mac 局域网 IP: $ip"
fi
echo "│  连接方式: $connection_hint"
echo "│  后端健康检查: http://127.0.0.1:${BACKEND_PORT}/bobo/health"
if [[ "$STARTED_BACKEND" == "true" ]]; then
echo "│  后端日志: $BACKEND_LOG"
fi
echo "│  Ctrl+C 退出                                    │"
echo "└─────────────────────────────────────────────────┘"
echo ""

if [[ "$mode" == "simulator" ]]; then
  open -a Simulator || true
  npx expo start --ios $clear_flag
else
  echo "[run-mobile] 请确认 iPhone 和 Mac 在同一 Wi-Fi。"
  npx expo start --lan $clear_flag
fi
