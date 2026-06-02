#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Enable temporary IPv4 NAT forwarding for one client host.

Usage:
  sudo scripts/setup-runtime-nat-gateway.sh --client-ip IP --lan-if IFACE --wan-if IFACE [options]

Options:
  --client-ip IP       Client IPv4 address to forward.
  --lan-if IFACE       Interface that reaches the client.
  --wan-if IFACE       Interface that reaches the upstream network.
  --lan-ip IP          Optional expected IPv4 address on --lan-if.
  --state-file PATH    State file for restore. Default: /tmp/xgc-runtime-nat-gateway.state
  -h, --help           Show this help.

The rules are runtime-only iptables rules. Reboot clears them unless another
service persists iptables.
EOF
}

CLIENT_IP="${CLIENT_IP:-}"
LAN_IF="${LAN_IF:-}"
LAN_IP="${LAN_IP:-}"
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
    --lan-ip)
      LAN_IP="${2:?missing value for $1}"
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

if [[ -z "${CLIENT_IP}" || -z "${LAN_IF}" || -z "${WAN_IF}" ]]; then
  echo "--client-ip, --lan-if and --wan-if are required" >&2
  usage >&2
  exit 2
fi

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo \
    CLIENT_IP="${CLIENT_IP}" \
    LAN_IF="${LAN_IF}" \
    LAN_IP="${LAN_IP}" \
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

require_cmd ip
require_cmd iptables
require_cmd sysctl

if [[ -n "${LAN_IP}" ]] && ! ip -4 addr show dev "${LAN_IF}" | grep -q "${LAN_IP}/"; then
  echo "expected ${LAN_IP} on ${LAN_IF}, but did not find it" >&2
  ip -br -4 addr show dev "${LAN_IF}" >&2 || true
  exit 1
fi

if ! ip link show dev "${LAN_IF}" >/dev/null 2>&1; then
  echo "LAN interface ${LAN_IF} does not exist" >&2
  ip -br addr >&2 || true
  exit 1
fi

if ! ip link show dev "${WAN_IF}" >/dev/null 2>&1; then
  echo "WAN interface ${WAN_IF} does not exist" >&2
  ip -br addr >&2 || true
  exit 1
fi

if [[ ! -e "${STATE_FILE}" ]]; then
  {
    echo "PREV_IP_FORWARD=$(sysctl -n net.ipv4.ip_forward)"
    printf 'CLIENT_IP=%q\n' "${CLIENT_IP}"
    printf 'LAN_IF=%q\n' "${LAN_IF}"
    printf 'LAN_IP=%q\n' "${LAN_IP}"
    printf 'WAN_IF=%q\n' "${WAN_IF}"
  } >"${STATE_FILE}"
fi

sysctl -w net.ipv4.ip_forward=1 >/dev/null

add_rule() {
  local table="$1"
  shift
  if [[ "${table}" == "filter" ]]; then
    iptables -C "$@" 2>/dev/null || iptables -A "$@"
  else
    iptables -t "${table}" -C "$@" 2>/dev/null || iptables -t "${table}" -A "$@"
  fi
}

add_rule nat POSTROUTING -s "${CLIENT_IP}/32" -o "${WAN_IF}" -j MASQUERADE
add_rule filter FORWARD -i "${LAN_IF}" -o "${WAN_IF}" -s "${CLIENT_IP}/32" -j ACCEPT
add_rule filter FORWARD -i "${WAN_IF}" -o "${LAN_IF}" -d "${CLIENT_IP}/32" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

echo "Runtime NAT is active for ${CLIENT_IP}."
echo "LAN: ${LAN_IF}${LAN_IP:+ ${LAN_IP}}; WAN: ${WAN_IF}"
echo "State saved at ${STATE_FILE}."
