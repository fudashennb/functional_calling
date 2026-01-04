#!/usr/bin/env bash

set -euo pipefail

# Source directory of packages to copy. Override by providing a path as $1.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEFAULT_PACKAGES_DIR="$SCRIPT_DIR/packages"
PACKAGES_DIR="${1:-$DEFAULT_PACKAGES_DIR}"

# Destination directory on the vehicles.
REMOTE_DIR="/home/root/packages"

# Credentials.
SSH_USER="root"
SSH_PASS="SRpasswd@2017"

# 高架车型IP列表 (车辆编号, IP地址)
GAOJIA_IPS=(
  "201:10.62.237.75"
  "202:10.62.237.199"
  "203:10.62.237.217"
  "204:10.62.237.130"
  "205:10.62.237.109"
  "206:10.62.237.165"
  "207:10.62.237.137"
  "208:10.62.237.192"
  "209:10.62.237.57"
  "210:10.62.237.76"
  "211:10.62.237.125"
  "212:10.62.237.30"
  "213:10.62.237.200"
)

# 堆垛车型IP列表 (车辆编号, IP地址)
DUODUO_IPS=(
  "1301:10.62.237.133"
  "1302:10.62.237.64"
  "1303:10.62.237.114"
  "1304:10.62.237.86"
  "1305:10.62.237.92"
  "1306:10.62.237.242"
  "1307:10.62.237.221"
  "1308:10.62.237.58"
  "1309:10.62.237.245"
  "1310:10.62.237.191"
  "1311:10.62.237.33"
  "1312:10.62.237.167"
  "1313:10.62.237.112"
  "1314:10.62.237.129"
  "1315:10.62.237.60"
  "1316:10.62.237.93"
  "1317:10.62.237.230"
  "1318:10.62.237.36"
  "1319:10.62.237.139"
)

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing dependency: $1" >&2
    exit 1
  fi
}

require_cmd scp

if [ ! -d "$PACKAGES_DIR" ]; then
  echo "Packages directory not found: $PACKAGES_DIR" >&2
  echo "Usage: $0 [/path/to/packages]" >&2
  exit 1
fi

copy_packages() {
  local label_ip="$1"
  local label="${label_ip%%:*}"
  local ip="${label_ip#*:}"

  echo "[$label][$ip] Copying packages..."
  local scp_opts=(
    -o StrictHostKeyChecking=no
    -o UserKnownHostsFile=/dev/null
    -o ConnectTimeout=8
  )

  # If an SSH key is provided, use it. Otherwise scp will prompt for password interactively.
  if [ -n "${SSH_KEY:-}" ]; then
    scp_opts+=(-i "$SSH_KEY")
  fi

  if scp "${scp_opts[@]}" -r "$PACKAGES_DIR" "${SSH_USER}@${ip}:$REMOTE_DIR"; then
    echo "[$label][$ip] Success"
  else
    echo "[$label][$ip] Failed" >&2
  fi
}

for entry in "${GAOJIA_IPS[@]}"; do
  copy_packages "$entry"
done

for entry in "${DUODUO_IPS[@]}"; do
  copy_packages "$entry"
done

echo "All copy tasks finished."

