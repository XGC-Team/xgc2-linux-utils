#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Prefer a temporary IPv4 default gateway on this host.

Usage:
  sudo scripts/use-runtime-gateway.sh --gateway IP [options]

Options:
  --gateway IP         Gateway IPv4 address to prefer for default traffic.
  --iface IFACE        Interface to use. Default: inferred from route to gateway.
  --metric VALUE       Route metric. Default: 50
  --direct-cidr CIDR   Optional directly connected CIDR to verify before changing default route.
  --dns IP[,IP...]     Optional comma-separated DNS servers for resolvectl.
  --state-file PATH    State file for restore. Default: /tmp/xgc-runtime-gateway.state
  -h, --help           Show this help.

The change is runtime-only. It does not edit Netplan, NetworkManager, or systemd
network configuration.
EOF
}

GATEWAY="${GATEWAY:-}"
IFACE="${IFACE:-}"
METRIC="${METRIC:-50}"
DIRECT_CIDR="${DIRECT_CIDR:-}"
DNS_SERVERS="${DNS_SERVERS:-}"
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
    --direct-cidr)
      DIRECT_CIDR="${2:?missing value for $1}"
      shift 2
      ;;
    --dns)
      DNS_SERVERS="${2:?missing value for $1}"
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

if [[ -z "${GATEWAY}" ]]; then
  echo "--gateway is required" >&2
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
  exec sudo \
    GATEWAY="${GATEWAY}" \
    IFACE="${IFACE}" \
    METRIC="${METRIC}" \
    DIRECT_CIDR="${DIRECT_CIDR}" \
    DNS_SERVERS="${DNS_SERVERS}" \
    STATE_FILE="${STATE_FILE}" \
    "$0" "$@"
fi

if ! ip link show dev "${IFACE}" >/dev/null 2>&1; then
  echo "interface ${IFACE} does not exist" >&2
  ip -br addr >&2 || true
  exit 1
fi

if [[ -n "${DIRECT_CIDR}" ]] && ! ip route show "${DIRECT_CIDR}" dev "${IFACE}" | grep -q "${DIRECT_CIDR}"; then
  echo "missing direct route for ${DIRECT_CIDR} on ${IFACE}; refusing to change default route" >&2
  ip route >&2
  exit 1
fi

{
  printf 'GATEWAY=%q\n' "${GATEWAY}"
  printf 'IFACE=%q\n' "${IFACE}"
  printf 'METRIC=%q\n' "${METRIC}"
} >"${STATE_FILE}"

ip route replace default via "${GATEWAY}" dev "${IFACE}" metric "${METRIC}"

if [[ -n "${DNS_SERVERS}" ]] && command -v resolvectl >/dev/null 2>&1; then
  IFS=',' read -r -a dns_array <<<"${DNS_SERVERS}"
  resolvectl dns "${IFACE}" "${dns_array[@]}" || true
  resolvectl domain "${IFACE}" '~.' || true
fi

echo "This host now prefers ${GATEWAY} for default traffic at metric ${METRIC}."
[[ -n "${DIRECT_CIDR}" ]] && echo "Verified direct route: ${DIRECT_CIDR} via ${IFACE}"
echo
ip route
