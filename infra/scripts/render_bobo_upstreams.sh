#!/usr/bin/env bash
set -euo pipefail

ACTIVE_SLOT="${1:-}"
CANARY_SLOT="${2:-}"
OUTPUT_PATH="${3:-}"

if [[ "$ACTIVE_SLOT" != "blue" && "$ACTIVE_SLOT" != "green" ]]; then
  echo "usage: $0 <blue|green> [canary-slot-or-empty] [output-path]" >&2
  exit 1
fi

if [[ -n "$CANARY_SLOT" && "$CANARY_SLOT" != "blue" && "$CANARY_SLOT" != "green" ]]; then
  echo "canary slot must be blue, green, or empty" >&2
  exit 1
fi

if [[ -z "$OUTPUT_PATH" ]]; then
  OUTPUT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/nginx/runtime/bobo-upstreams.conf"
fi

ACTIVE_UPSTREAM="http://backend_${ACTIVE_SLOT}:8000"

if [[ -n "$CANARY_SLOT" && "$CANARY_SLOT" != "$ACTIVE_SLOT" ]]; then
  CANARY_UPSTREAM="http://backend_${CANARY_SLOT}:8000"
  cat >"$OUTPUT_PATH" <<EOF
map \$http_x_bobo_canary \$bobo_canary_from_header {
    default 0;
    "1" 1;
    "true" 1;
    "TRUE" 1;
    "yes" 1;
}

map \$cookie_bobo_canary \$bobo_canary_from_cookie {
    default 0;
    "1" 1;
    "true" 1;
    "TRUE" 1;
    "yes" 1;
}

map "\$bobo_canary_from_header:\$bobo_canary_from_cookie" \$bobo_backend {
    default ${ACTIVE_UPSTREAM};
    "~^1:" ${CANARY_UPSTREAM};
    "~^0:1$" ${CANARY_UPSTREAM};
}
EOF
else
  cat >"$OUTPUT_PATH" <<EOF
map \$request_uri \$bobo_backend {
    default ${ACTIVE_UPSTREAM};
}
EOF
fi

echo "[render-upstreams] active=${ACTIVE_SLOT} canary=${CANARY_SLOT:-none} -> ${OUTPUT_PATH}"
