#!/usr/bin/env bash
# Enable or restore network time sync. Shared by all robots.
# Usage: sudo configure-time-sync.sh [--servers "host1 host2 ..."]
#        sudo configure-time-sync.sh --restore
set -euo pipefail

STATE_DIR="${XGC2_UTILS_STATE:-/var/lib/xgc2-utils/state/time}"
DROPIN="/etc/systemd/timesyncd.conf.d/xgc2-ntp.conf"
DROPIN_BAK="${STATE_DIR}/timesyncd.xgc2-ntp.conf.prev"
NTP_WAS="${STATE_DIR}/ntp.was"
SERVERS="ntp.aliyun.com ntp.tencent.com ntp.ntsc.ac.cn"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --servers)
      SERVERS="${2:?missing value for --servers}"
      shift 2
      ;;
    --restore)
      RESTORE=1
      shift
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  exec sudo "$0" ${RESTORE:+--restore} ${SERVERS:+--servers "${SERVERS}"}
fi

mkdir -p "${STATE_DIR}" /etc/systemd/timesyncd.conf.d

if [[ "${RESTORE:-0}" == "1" ]]; then
  if [[ -e "${DROPIN_BAK}" ]]; then
    cp -a "${DROPIN_BAK}" "${DROPIN}"
  else
    rm -f "${DROPIN}"
  fi
  if [[ -s "${NTP_WAS}" ]] && command -v timedatectl >/dev/null 2>&1; then
    case "$(cat "${NTP_WAS}")" in
      yes|true|1) timedatectl set-ntp true || true ;;
      *) timedatectl set-ntp false || true ;;
    esac
  fi
  if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
    systemctl restart systemd-timesyncd.service >/dev/null 2>&1 || true
  fi
  echo "ntp=restored"
  exit 0
fi

if command -v timedatectl >/dev/null 2>&1; then
  timedatectl show -p NTP --value > "${NTP_WAS}" 2>/dev/null || echo unknown > "${NTP_WAS}"
  timedatectl set-ntp true || true
fi

if [[ -e "${DROPIN}" && ! -e "${DROPIN_BAK}" ]]; then
  cp -a "${DROPIN}" "${DROPIN_BAK}"
fi

cat > "${DROPIN}" <<EOF
[Time]
NTP=${SERVERS}
FallbackNTP=ntp.ubuntu.com
EOF

if command -v systemctl >/dev/null 2>&1; then
  systemctl enable systemd-timesyncd.service >/dev/null 2>&1 || true
  if [[ -d /run/systemd/system ]]; then
    systemctl restart systemd-timesyncd.service >/dev/null 2>&1 || true
  fi
fi

echo "ntp=on servers=${SERVERS}"
