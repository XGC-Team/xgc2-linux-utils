#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Remove temporary IPv4 NAT forwarding rules created by setup-runtime-nat-gateway.sh.

Usage:
  sudo scripts/restore-runtime-nat-gateway.sh [options]

Options:
  --client-ip IP       Client IPv4 address to stop forwarding.
  --lan-if IFACE       Interface that reaches the client.
  --wan-if IFACE       Interface that reaches the upstream network.
  --state-file PATH    State file written by setup. Default: /tmp/xgc-runtime-nat-gateway.state
  -h, --help           Show this help.

If the state file exists, missing options are loaded from it.
EOF
}

CLIENT_IP="${CLIENT_IP:-}"
LAN_IF="${LAN_IF:-}"
WAN_IF="${WAN_IF:-}"
STATE_FILE="${STATE_FILE:-/tmp/xgc-runtime-nat-gateway.state}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --client-ip)
      CLIENT_IP="${2:?missing value for $1}"
      shift 2
      ;;
    --lan-if)
      LAN_IF="${2:?missing value for $1}"
      shift 2
      ;;
    --wan-if)
      WAN_IF="${2:?missing value for $1}"
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

if [[ -z "${CLIENT_IP}" || -z "${LAN_IF}" || -z "${WAN_IF}" ]]; then
  echo "--client-ip, --lan-if and --wan-if are required when no complete state file exists" >&2
  usage >&2
  exit 2
fi

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo \
    CLIENT_IP="${CLIENT_IP}" \
    LAN_IF="${LAN_IF}" \
    WAN_IF="${WAN_IF}" \
    STATE_FILE="${STATE_FILE}" \
    "$0" "$@"
fi

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required command: $1" >&2
    exit 1
  }
}

require_cmd iptables
require_cmd sysctl

delete_rule() {
  local table="$1"
  shift
  if [[ "${table}" == "filter" ]]; then
    while iptables -C "$@" 2>/dev/null; do
      iptables -D "$@"
    done
  else
    while iptables -t "${table}" -C "$@" 2>/dev/null; do
      iptables -t "${table}" -D "$@"
    done
  fi
}

delete_rule nat POSTROUTING -s "${CLIENT_IP}/32" -o "${WAN_IF}" -j MASQUERADE
delete_rule filter FORWARD -i "${LAN_IF}" -o "${WAN_IF}" -s "${CLIENT_IP}/32" -j ACCEPT
delete_rule filter FORWARD -i "${WAN_IF}" -o "${LAN_IF}" -d "${CLIENT_IP}/32" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

if [[ "${PREV_IP_FORWARD:-}" =~ ^[01]$ ]]; then
  sysctl -w "net.ipv4.ip_forward=${PREV_IP_FORWARD}" >/dev/null
fi

rm -f "${STATE_FILE}"

echo "Runtime NAT rules for ${CLIENT_IP} have been removed."
