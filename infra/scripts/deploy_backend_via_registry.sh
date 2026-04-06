#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVER_ALIAS="${1:-tx-server}"
IMAGE_TAG="${2:-$(git -C "$ROOT_DIR" rev-parse --short HEAD)}"

echo "[deploy-registry] 已切换为蓝绿发布入口，开始部署到备用槽位"
bash "$ROOT_DIR/infra/scripts/blue_green_release.sh" "$SERVER_ALIAS" "" "$IMAGE_TAG"
