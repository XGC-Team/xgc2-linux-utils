#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Remove a temporary default route created by use-runtime-gateway.sh.

Usage:
  sudo scripts/restore-runtime-gateway.sh [options]

Options:
  --gateway IP         Gateway IPv4 address to remove.
  --iface IFACE        Interface to use. Default: inferred from route to gateway or state file.
  --metric VALUE       Route metric. Default: 50
  --state-file PATH    State file written by setup. Default: /tmp/xgc-runtime-gateway.state
  -h, --help           Show this help.
EOF
}

GATEWAY="${GATEWAY:-}"
IFACE="${IFACE:-}"
METRIC="${METRIC:-50}"
STATE_FILE="${STATE_FILE:-/tmp/xgc-runtime-gateway.state}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gateway)
      GATEWAY="${2:?missing value for $1}"
      shift 2
      ;;
    --iface)
      IFACE="${2:?missing value for $1}"
      shift 2
      ;;
    --metric)
      METRIC="${2:?missing value for $1}"
      shift 2
      ;;
    --state-file)
      STATE_FILE="${2:?missing value for $1}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -f "${STATE_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${STATE_FILE}"
fi

if [[ -z "${GATEWAY}" ]]; then
  echo "--gateway is required when no state file exists" >&2
  usage >&2
  exit 2
fi

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required command: $1" >&2
    exit 1
  }
}

require_cmd ip

if [[ -z "${IFACE}" ]]; then
  IFACE="$(ip -o route get "${GATEWAY}" 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "dev") {print $(i + 1); exit}}')"
fi

if [[ -z "${IFACE}" ]]; then
  echo "could not infer interface for gateway ${GATEWAY}; pass --iface" >&2
  ip -br -4 addr >&2 || true
  exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo IFACE="${IFACE}" GATEWAY="${GATEWAY}" METRIC="${METRIC}" STATE_FILE="${STATE_FILE}" "$0" "$@"
fi

while ip route del default via "${GATEWAY}" dev "${IFACE}" metric "${METRIC}" 2>/dev/null; do
  :
done

while ip route del default via "${GATEWAY}" dev "${IFACE}" 2>/dev/null; do
  :
done

if command -v resolvectl >/dev/null 2>&1; then
  resolvectl revert "${IFACE}" || true
fi

rm -f "${STATE_FILE}"

echo "Runtime route through ${GATEWAY} has been removed."
echo
ip route
