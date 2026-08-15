#!/usr/bin/env bash
# Autologin to the graphical desktop and disable the lock screen.
# Shared by all robots. Applies on install; --restore puts the host back.
# Usage: sudo configure-desktop-open.sh [--user NAME]
#        sudo configure-desktop-open.sh --restore
#        configure-desktop-open.sh --print-user
set -euo pipefail

STATE_DIR="${XGC2_UTILS_STATE:-/var/lib/xgc2-utils/state/desktop}"
DCONF_DROPIN="/etc/dconf/db/local.d/90-xgc2-desktop-open"
DCONF_PROFILE="/etc/dconf/profile/user"
LIGHTDM_DROPIN="/etc/lightdm/lightdm.conf.d/90-xgc2-autologin.conf"
SESSION_USER="${XGC2_DESKTOP_USER:-}"
RESTORE=0
PRINT_USER=0

usage() {
  cat <<'EOF'
Usage: configure-desktop-open.sh [--user NAME]
       configure-desktop-open.sh --restore
       configure-desktop-open.sh --print-user

Enable display-manager autologin and turn off the GNOME/Unity lock screen.
Default user is XGC2_DESKTOP_USER, otherwise uid 1000, otherwise the first
interactive local account. Does not restart gdm/lightdm; autologin is for
the next boot. Lock-screen settings apply immediately when a session exists.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)
      SESSION_USER="${2:?--user requires a name}"
      shift 2
      ;;
    --restore)
      RESTORE=1
      shift
      ;;
    --print-user)
      PRINT_USER=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

detect_user() {
  local name shell
  if [[ -n "${SESSION_USER}" ]]; then
    if ! getent passwd "${SESSION_USER}" >/dev/null; then
      echo "desktop user '${SESSION_USER}' is not a local account" >&2
      return 1
    fi
    printf '%s\n' "${SESSION_USER}"
    return 0
  fi
  name="$(getent passwd 1000 | cut -d: -f1 || true)"
  if [[ -n "${name}" ]]; then
    printf '%s\n' "${name}"
    return 0
  fi
  while IFS=: read -r name _ uid _ _ _ shell; do
    [[ "${uid}" -ge 1000 && "${uid}" -lt 65534 ]] || continue
    case "${shell}" in
      */nologin|*/false) continue ;;
    esac
    printf '%s\n' "${name}"
    return 0
  done < /etc/passwd
  echo "could not detect a desktop login user; pass --user" >&2
  return 1
}

detect_dm() {
  local dm=""
  if [[ -r /etc/X11/default-display-manager ]]; then
    dm="$(tr -d ' \t\r' < /etc/X11/default-display-manager)"
  fi
  case "${dm}" in
    */gdm3) echo gdm3; return ;;
    */gdm) echo gdm; return ;;
    */lightdm) echo lightdm; return ;;
  esac
  if [[ -x /usr/sbin/gdm3 || -f /etc/gdm3/custom.conf ]]; then
    echo gdm3
  elif [[ -x /usr/sbin/gdm || -f /etc/gdm/custom.conf ]]; then
    echo gdm
  elif [[ -x /usr/sbin/lightdm || -d /etc/lightdm ]]; then
    echo lightdm
  else
    echo none
  fi
}

gdm_conf_path() {
  if [[ -f /etc/gdm3/custom.conf ]]; then
    printf '%s\n' /etc/gdm3/custom.conf
  elif [[ -f /etc/gdm/custom.conf ]]; then
    printf '%s\n' /etc/gdm/custom.conf
  else
    return 1
  fi
}

apply_gdm_autologin() {
  local user="$1"
  local conf tmp
  conf="$(gdm_conf_path)" || return 1
  if [[ ! -e "${STATE_DIR}/gdm-custom.conf.prev" ]]; then
    cp -a "${conf}" "${STATE_DIR}/gdm-custom.conf.prev"
  fi
  tmp="$(mktemp "${TMPDIR:-/tmp}/xgc2-gdm.XXXXXX")"
  awk -v user="${user}" '
    BEGIN { in_daemon=0; have_enable=0; have_user=0; have_daemon=0 }
    /^\[daemon\]/ {
      in_daemon=1
      have_daemon=1
      print
      next
    }
    /^\[/ {
      if (in_daemon) {
        if (!have_enable) print "AutomaticLoginEnable=true"
        if (!have_user) print "AutomaticLogin=" user
      }
      in_daemon=0
      print
      next
    }
    in_daemon && /^[ \t]*#?[ \t]*AutomaticLoginEnable[ \t]*=/ {
      if (have_enable) next
      print "AutomaticLoginEnable=true"
      have_enable=1
      next
    }
    in_daemon && /^[ \t]*#?[ \t]*AutomaticLogin[ \t]*=/ {
      if (have_user) next
      print "AutomaticLogin=" user
      have_user=1
      next
    }
    { print }
    END {
      if (in_daemon || !have_daemon) {
        if (!have_daemon) print "[daemon]"
        if (!have_enable) print "AutomaticLoginEnable=true"
        if (!have_user) print "AutomaticLogin=" user
      }
    }
  ' "${conf}" > "${tmp}"
  install -m 0644 "${tmp}" "${conf}"
  rm -f "${tmp}"
}

apply_lightdm_autologin() {
  local user="$1"
  install -d -m 0755 /etc/lightdm/lightdm.conf.d
  cat > "${LIGHTDM_DROPIN}" <<EOF
[Seat:*]
autologin-user=${user}
autologin-user-timeout=0
EOF
  chmod 0644 "${LIGHTDM_DROPIN}"
}

apply_dconf() {
  install -d -m 0755 /etc/dconf/profile /etc/dconf/db/local.d
  if [[ ! -e "${STATE_DIR}/dconf-profile.user.prev" && -e "${DCONF_PROFILE}" ]]; then
    cp -a "${DCONF_PROFILE}" "${STATE_DIR}/dconf-profile.user.prev"
  fi
  if [[ ! -e "${DCONF_PROFILE}" ]]; then
    cat > "${DCONF_PROFILE}" <<'EOF'
user-db:user
system-db:local
EOF
  elif ! grep -qx 'system-db:local' "${DCONF_PROFILE}"; then
    printf 'system-db:local\n' >> "${DCONF_PROFILE}"
  fi
  cat > "${DCONF_DROPIN}" <<'EOF'
[org/gnome/desktop/session]
idle-delay=uint32 0

[org/gnome/desktop/screensaver]
idle-activation-enabled=false
lock-enabled=false
lock-delay=uint32 0
ubuntu-lock-on-suspend=false

[org/gnome/desktop/lockdown]
disable-lock-screen=true

[org/gnome/settings-daemon/plugins/power]
idle-dim=false
sleep-inactive-ac-type='nothing'
sleep-inactive-battery-type='nothing'
sleep-inactive-ac-timeout=0
EOF
  chmod 0644 "${DCONF_DROPIN}"
  if command -v dconf >/dev/null 2>&1; then
    dconf update >/dev/null 2>&1 || true
  fi
}

apply_live_gsettings() {
  local user_name="$1"
  local uid
  uid="$(id -u "${user_name}" 2>/dev/null || true)"
  [[ -n "${uid}" ]] || return 0

  run_gs() {
    local prefix=()
    if [[ -S "/run/user/${uid}/bus" ]]; then
      prefix=(
        sudo -u "${user_name}"
        env DISPLAY="${DISPLAY:-:0}"
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${uid}/bus"
      )
    else
      prefix=(sudo -u "${user_name}" dbus-run-session)
    fi
    "${prefix[@]}" gsettings "$@" >/dev/null 2>&1 || true
  }

  command -v gsettings >/dev/null 2>&1 || return 0
  run_gs set org.gnome.desktop.screensaver lock-enabled false
  run_gs set org.gnome.desktop.screensaver idle-activation-enabled false
  run_gs set org.gnome.desktop.screensaver ubuntu-lock-on-suspend false
  run_gs set org.gnome.desktop.session idle-delay 0
  run_gs set org.gnome.desktop.lockdown disable-lock-screen true
  run_gs set org.gnome.settings-daemon.plugins.power idle-dim false
  run_gs set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type nothing
  run_gs set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type nothing
  run_gs set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-timeout 0
  run_gs set com.canonical.unity.settings-daemon.plugins.power sleep-inactive-ac-type nothing
  run_gs set com.canonical.unity.settings-daemon.plugins.power sleep-inactive-battery-type nothing
}

restore_all() {
  local conf
  if [[ -e "${STATE_DIR}/gdm-custom.conf.prev" ]]; then
    if conf="$(gdm_conf_path)"; then
      cp -a "${STATE_DIR}/gdm-custom.conf.prev" "${conf}"
    fi
  fi
  rm -f "${LIGHTDM_DROPIN}"
  rm -f "${DCONF_DROPIN}"
  if [[ -e "${STATE_DIR}/dconf-profile.user.prev" ]]; then
    cp -a "${STATE_DIR}/dconf-profile.user.prev" "${DCONF_PROFILE}"
  fi
  if command -v dconf >/dev/null 2>&1; then
    dconf update >/dev/null 2>&1 || true
  fi
  echo "desktop-open=restored"
}

if [[ "${PRINT_USER}" == "1" ]]; then
  detect_user
  exit 0
fi

if [[ "$(id -u)" -ne 0 ]]; then
  exec sudo "$0" "$@"
fi

if [[ "${RESTORE}" == "1" ]]; then
  restore_all
  exit 0
fi

user="$(detect_user)"
mkdir -p "${STATE_DIR}"
printf '%s\n' "${user}" > "${STATE_DIR}/user"

dm="$(detect_dm)"
case "${dm}" in
  gdm3|gdm) apply_gdm_autologin "${user}" ;;
  lightdm) apply_lightdm_autologin "${user}" ;;
  none) echo "no display manager; writing lock-screen policy only" ;;
esac
apply_dconf
apply_live_gsettings "${user}"
echo "desktop-open=autologin:${user} dm:${dm} lock=off"
