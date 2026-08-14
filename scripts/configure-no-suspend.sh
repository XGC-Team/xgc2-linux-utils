#!/usr/bin/env bash
# Disable or restore host suspend. Shared by all robots.
# Usage: sudo configure-no-suspend.sh
#        sudo configure-no-suspend.sh --restore
set -euo pipefail

STATE_DIR="${XGC2_UTILS_STATE:-/var/lib/xgc2-utils/state/sleep}"
DROPIN="/etc/systemd/logind.conf.d/xgc2-no-suspend.conf"
DROPIN_BAK="${STATE_DIR}/xgc2-no-suspend.conf.prev"

if [[ "$(id -u)" -ne 0 ]]; then
  exec sudo "$0" "$@"
fi

mkdir -p "${STATE_DIR}" /etc/systemd/logind.conf.d

reload_logind() {
  if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
    systemctl restart systemd-logind.service >/dev/null 2>&1 || true
  fi
}

if [[ "${1:-}" == "--restore" ]]; then
  if [[ -e "${DROPIN_BAK}" ]]; then
    cp -a "${DROPIN_BAK}" "${DROPIN}"
  else
    rm -f "${DROPIN}"
  fi
  reload_logind
  echo "suspend=restored"
  exit 0
fi

if [[ -e "${DROPIN}" && ! -e "${DROPIN_BAK}" ]]; then
  cp -a "${DROPIN}" "${DROPIN_BAK}"
fi

cat > "${DROPIN}" <<'EOF'
[Login]
HandleSuspendKey=ignore
HandleHibernateKey=ignore
HandleLidSwitch=ignore
HandleLidSwitchDocked=ignore
IdleAction=ignore
EOF

reload_logind
echo "suspend=disabled"
