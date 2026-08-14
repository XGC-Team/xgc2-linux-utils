#!/usr/bin/env bash
# Set the host timezone. Shared by all robots.
# Usage: sudo configure-timezone.sh [city|Region/City]
#        sudo configure-timezone.sh --restore
set -euo pipefail

STATE_DIR="${XGC2_UTILS_STATE:-/var/lib/xgc2-utils/state/time}"
STATE_FILE="${STATE_DIR}/timezone.prev"

resolve_zone() {
  local raw="${1:-shanghai}"
  raw="$(printf '%s' "${raw}" | tr '[:upper:]' '[:lower:]')"
  case "${raw}" in
    shanghai|china|cn|beijing|prc) echo Asia/Shanghai ;;
    utc|gmt|zulu) echo UTC ;;
    tokyo|japan|jp) echo Asia/Tokyo ;;
    seoul|korea|kr) echo Asia/Seoul ;;
    singapore|sg) echo Asia/Singapore ;;
    hongkong|hk) echo Asia/Hong_Kong ;;
    taipei|tw) echo Asia/Taipei ;;
    london|uk) echo Europe/London ;;
    paris|fr) echo Europe/Paris ;;
    berlin|de) echo Europe/Berlin ;;
    nyc|newyork|us-east) echo America/New_York ;;
    la|losangeles|us-west) echo America/Los_Angeles ;;
    *)
      # Accept a real IANA name if the user already knows it.
      if [[ -e "/usr/share/zoneinfo/${1}" ]]; then
        echo "${1}"
        return 0
      fi
      echo "unknown timezone '${1}'. try: shanghai utc tokyo seoul singapore hongkong" >&2
      return 1
      ;;
  esac
}

current_zone() {
  if command -v timedatectl >/dev/null 2>&1; then
    timedatectl show -p Timezone --value 2>/dev/null && return 0
  fi
  if [[ -L /etc/localtime ]]; then
    readlink -f /etc/localtime | sed 's|.*/zoneinfo/||'
    return 0
  fi
  cat /etc/timezone 2>/dev/null || echo UTC
}

apply_zone() {
  local zone="$1"
  if command -v timedatectl >/dev/null 2>&1; then
    timedatectl set-timezone "${zone}"
  else
    ln -sfn "/usr/share/zoneinfo/${zone}" /etc/localtime
    printf '%s\n' "${zone}" > /etc/timezone
  fi
}

if [[ "$(id -u)" -ne 0 ]]; then
  exec sudo "$0" "$@"
fi

if [[ "${1:-}" == "--restore" ]]; then
  if [[ ! -s "${STATE_FILE}" ]]; then
    echo "no saved timezone to restore" >&2
    exit 1
  fi
  apply_zone "$(cat "${STATE_FILE}")"
  echo "timezone=$(current_zone) (restored)"
  exit 0
fi

ZONE="$(resolve_zone "${1:-shanghai}")"
mkdir -p "${STATE_DIR}"
if [[ ! -s "${STATE_FILE}" ]]; then
  current_zone > "${STATE_FILE}"
fi
apply_zone "${ZONE}"
echo "timezone=${ZONE}"
