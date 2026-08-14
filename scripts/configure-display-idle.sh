#!/usr/bin/env bash
# GNOME idle / lock defaults via system dconf. Shared by all robots.
# Usage: sudo configure-display-idle.sh [--idle-seconds N] [--user NAME]
#        sudo configure-display-idle.sh --restore
set -euo pipefail

STATE_DIR="${XGC2_UTILS_STATE:-/var/lib/xgc2-utils/state/screen}"
DROPIN="/etc/dconf/db/local.d/00-xgc2-display-idle"
DROPIN_BAK="${STATE_DIR}/00-xgc2-display-idle.prev"
PROFILE="/etc/dconf/profile/user"
PROFILE_BAK="${STATE_DIR}/dconf-profile.user.prev"
IDLE_SECONDS=1800
SESSION_USER=""
RESTORE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --idle-seconds)
      IDLE_SECONDS="${2:?missing value for --idle-seconds}"
      shift 2
      ;;
    --user)
      SESSION_USER="${2:?missing value for --user}"
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
  exec sudo "$0" \
    ${RESTORE:+--restore} \
    --idle-seconds "${IDLE_SECONDS}" \
    ${SESSION_USER:+--user "${SESSION_USER}"}
fi

mkdir -p "${STATE_DIR}" /etc/dconf/profile /etc/dconf/db/local.d

refresh_dconf() {
  if command -v dconf >/dev/null 2>&1; then
    dconf update >/dev/null 2>&1 || true
  fi
}

if [[ "${RESTORE}" == "1" ]]; then
  if [[ -e "${DROPIN_BAK}" ]]; then
    cp -a "${DROPIN_BAK}" "${DROPIN}"
  else
    rm -f "${DROPIN}"
  fi
  if [[ -e "${PROFILE_BAK}" ]]; then
    cp -a "${PROFILE_BAK}" "${PROFILE}"
  fi
  refresh_dconf
  echo "display-idle=restored"
  exit 0
fi

if [[ -e "${DROPIN}" && ! -e "${DROPIN_BAK}" ]]; then
  cp -a "${DROPIN}" "${DROPIN_BAK}"
fi
if [[ -e "${PROFILE}" && ! -e "${PROFILE_BAK}" ]]; then
  cp -a "${PROFILE}" "${PROFILE_BAK}"
fi

if [[ ! -e "${PROFILE}" ]]; then
  cat > "${PROFILE}" <<'EOF'
user-db:user
system-db:local
EOF
fi

cat > "${DROPIN}" <<EOF
[org/gnome/desktop/session]
idle-delay=uint32 ${IDLE_SECONDS}

[org/gnome/desktop/screensaver]
idle-activation-enabled=true
lock-enabled=true
lock-delay=uint32 0

[org/gnome/settings-daemon/plugins/power]
idle-dim=false
sleep-inactive-ac-type='nothing'
sleep-inactive-battery-type='nothing'
EOF

refresh_dconf

apply_live() {
  local user_name="$1"
  local uid
  uid="$(id -u "${user_name}" 2>/dev/null || true)"
  [[ -n "${uid}" ]] || return 0
  [[ -S "/run/user/${uid}/bus" ]] || return 0
  sudo -u "${user_name}" \
    env DISPLAY="${DISPLAY:-:0}" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${uid}/bus" \
    gsettings set org.gnome.desktop.session idle-delay "${IDLE_SECONDS}" >/dev/null 2>&1 || true
}

if [[ -n "${SESSION_USER}" ]]; then
  apply_live "${SESSION_USER}"
fi

echo "display-idle=${IDLE_SECONDS}s"
