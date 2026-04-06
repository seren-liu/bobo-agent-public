#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVER_ALIAS="${1:-tx-server}"
REMOTE_ROOT="${DEPLOY_REMOTE_ROOT:-/opt/bobo}"
DEPLOY_ARCHIVE="/tmp/bobo-deploy.tgz"
TOP_LEVELS_FILE="/tmp/bobo-deploy-top-level.txt"

ROOT_DIR="$ROOT_DIR" python - <<'PY' >"$TOP_LEVELS_FILE"
import os
import subprocess

root_dir = os.environ["ROOT_DIR"]
output = subprocess.check_output(["git", "ls-files", "-z"], cwd=root_dir)
tracked = [
    item.decode("utf-8")
    for item in output.split(b"\0")
    if item and os.path.exists(os.path.join(root_dir, item.decode("utf-8")))
]
for item in sorted({path.split("/", 1)[0] for path in tracked}):
    print(item)
PY

(
  cd "$ROOT_DIR"
  ROOT_DIR="$ROOT_DIR" python - <<'PY' | tar --null -T - -czf "$DEPLOY_ARCHIVE"
import os
import subprocess

root_dir = os.environ["ROOT_DIR"]
output = subprocess.check_output(["git", "ls-files", "-z"], cwd=root_dir)
for item in output.split(b"\0"):
    if not item:
        continue
    path = item.decode("utf-8")
    if os.path.exists(os.path.join(root_dir, path)):
        print(path, end="\0")
PY
)
scp "$DEPLOY_ARCHIVE" "$TOP_LEVELS_FILE" "$SERVER_ALIAS:/tmp/"

ssh "$SERVER_ALIAS" "REMOTE_ROOT='$REMOTE_ROOT' bash -s" <<'REMOTE'
set -euo pipefail
mkdir -p "$REMOTE_ROOT"
while IFS= read -r path; do
  [ -n "$path" ] || continue
  rm -rf "$REMOTE_ROOT/$path"
done </tmp/bobo-deploy-top-level.txt

tar -xzf /tmp/bobo-deploy.tgz -C "$REMOTE_ROOT"
rm -f /tmp/bobo-deploy.tgz /tmp/bobo-deploy-top-level.txt
REMOTE

rm -f "$DEPLOY_ARCHIVE" "$TOP_LEVELS_FILE"
echo "[sync-source] synced tracked files to $SERVER_ALIAS:$REMOTE_ROOT"
