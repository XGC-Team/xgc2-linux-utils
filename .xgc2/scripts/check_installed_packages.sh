#!/usr/bin/env bash
set -euo pipefail

PERF_PACKAGE="xgc2-utils-linux-performance-mode"
TZ_PACKAGE="xgc2-utils-linux-timezone"
DESKTOP_PACKAGE="xgc2-utils-linux-desktop"
LIB_DIR="/usr/lib/xgc2-utils/linux"
DEB_DIR=""
DEB_PATH=""

usage() {
  cat <<EOF
Usage: ${0##*/} --deb-dir DIR
       ${0##*/} --deb PATH

Validate package metadata and payload without installing the packages.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deb-dir)
      DEB_DIR="$2"
      shift 2
      ;;
    --deb)
      DEB_PATH="$2"
      shift 2
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

if [[ -z "${DEB_DIR}" && -z "${DEB_PATH}" ]]; then
  echo "--deb-dir or --deb is required" >&2
  usage >&2
  exit 2
fi

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "${tmp_dir}"
}
trap cleanup EXIT

find_deb() {
  local package="$1"
  local match=""
  local path
  if [[ -n "${DEB_PATH}" ]]; then
    if dpkg-deb --field "${DEB_PATH}" Package | grep -Fxq "${package}"; then
      printf '%s\n' "${DEB_PATH}"
      return 0
    fi
    echo "missing ${package} in ${DEB_PATH}" >&2
    return 1
  fi
  shopt -s nullglob
  for path in "${DEB_DIR}"/*.deb; do
    if dpkg-deb --field "${path}" Package | grep -Fxq "${package}"; then
      if [[ -n "${match}" ]]; then
        echo "multiple ${package} debs in ${DEB_DIR}" >&2
        return 1
      fi
      match="${path}"
    fi
  done
  shopt -u nullglob
  if [[ -z "${match}" ]]; then
    echo "missing ${package} in ${DEB_DIR}" >&2
    return 1
  fi
  printf '%s\n' "${match}"
}

extract_deb() {
  local deb="$1"
  local dest="$2"
  mkdir -p "${dest}/root" "${dest}/control"
  dpkg-deb --extract "${deb}" "${dest}/root"
  dpkg-deb --control "${deb}" "${dest}/control"
}

check_timezone_deb() {
  local deb
  deb="$(find_deb "${TZ_PACKAGE}")"
  dpkg-deb --field "${deb}" Architecture | grep -Fx all >/dev/null
  dpkg-deb --field "${deb}" Depends | grep -F "tzdata" >/dev/null
  extract_deb "${deb}" "${tmp_dir}/timezone"

  test -x "${tmp_dir}/timezone/root${LIB_DIR}/configure-timezone.sh"
  test -x "${tmp_dir}/timezone/root${LIB_DIR}/configure-time-sync.sh"
  test ! -e "${tmp_dir}/timezone/root/usr/bin/xcli"
  test ! -e "${tmp_dir}/timezone/root${LIB_DIR}/xcli"
  test ! -e "${tmp_dir}/timezone/root${LIB_DIR}/enable_performance_mode.sh"
  test ! -e "${tmp_dir}/timezone/root${LIB_DIR}/configure-desktop-open.sh"
  test ! -d "${tmp_dir}/timezone/root/lib/systemd/system"
  test -x "${tmp_dir}/timezone/control/postinst"
  test -x "${tmp_dir}/timezone/control/prerm"
  test -x "${tmp_dir}/timezone/control/postrm"
  grep -F 'configure-timezone.sh" shanghai' "${tmp_dir}/timezone/control/postinst" >/dev/null
  grep -F "configure-time-sync.sh" "${tmp_dir}/timezone/control/postinst" >/dev/null
  grep -F "configure-timezone.sh\" --restore" "${tmp_dir}/timezone/control/prerm" >/dev/null
  if grep -E "systemctl enable|WantedBy=" "${tmp_dir}/timezone/control/postinst" >/dev/null; then
    echo "timezone package must not enable a boot unit" >&2
    exit 1
  fi
}

check_performance_deb() {
  local deb
  deb="$(find_deb "${PERF_PACKAGE}")"
  dpkg-deb --field "${deb}" Architecture | grep -Fx all >/dev/null
  dpkg-deb --field "${deb}" Depends | grep -F "cpufrequtils" >/dev/null
  dpkg-deb --field "${deb}" Depends | grep -F "${TZ_PACKAGE}" >/dev/null
  extract_deb "${deb}" "${tmp_dir}/perf"

  test -r "${tmp_dir}/perf/root${LIB_DIR}/common.sh"
  test -x "${tmp_dir}/perf/root${LIB_DIR}/xcli"
  test -L "${tmp_dir}/perf/root/usr/bin/xcli"
  test -r "${tmp_dir}/perf/root${LIB_DIR}/xcli_eval.py"
  test -r "${tmp_dir}/perf/root${LIB_DIR}/xcli_eval_host.py"
  test -r "${tmp_dir}/perf/root${LIB_DIR}/xcli_eval_ros.py"
  test -r "${tmp_dir}/perf/root${LIB_DIR}/xcli_eval_mav.py"
  test -r "${tmp_dir}/perf/root/usr/share/bash-completion/completions/xcli"
  test ! -e "${tmp_dir}/perf/root${LIB_DIR}/configure-timezone.sh"
  test ! -e "${tmp_dir}/perf/root${LIB_DIR}/configure-time-sync.sh"
  test ! -e "${tmp_dir}/perf/root${LIB_DIR}/configure-desktop-open.sh"
  for script in \
    configure-display-idle.sh \
    configure-log-limits.sh \
    configure-no-suspend.sh \
    enable_performance_mode.sh \
    print_cpu_frequency.sh \
    query_cpu_state.sh \
    restore-runtime-gateway.sh \
    restore-runtime-nat-gateway.sh \
    restore_balanced_mode.sh \
    setup-runtime-nat-gateway.sh \
    use-runtime-gateway.sh; do
    test -x "${tmp_dir}/perf/root${LIB_DIR}/${script}"
  done

  test -r "${tmp_dir}/perf/root/usr/share/${PERF_PACKAGE}/cpufrequtils.default"
  grep -Fx 'GOVERNOR="performance"' \
    "${tmp_dir}/perf/root/usr/share/${PERF_PACKAGE}/cpufrequtils.default" >/dev/null
  if find "${tmp_dir}/perf/root/lib/systemd/system" -maxdepth 1 \
    -name 'xgc2-utils-linux-performance-mode.service' 2>/dev/null | grep -q .; then
    echo "package must not ship custom performance-mode systemd service" >&2
    exit 1
  fi

  test -x "${tmp_dir}/perf/control/postinst"
  test -x "${tmp_dir}/perf/control/prerm"
  test -x "${tmp_dir}/perf/control/postrm"
  grep -F "systemctl_quiet disable ondemand.service" "${tmp_dir}/perf/control/postinst" >/dev/null
  grep -F "systemctl_quiet enable cpufrequtils.service" "${tmp_dir}/perf/control/postinst" >/dev/null
  grep -F 'GOVERNOR="performance"' "${tmp_dir}/perf/control/postinst" >/dev/null
  grep -F "systemctl_quiet stop cpufrequtils.service" "${tmp_dir}/perf/control/prerm" >/dev/null
  grep -F "systemctl_quiet enable ondemand.service" "${tmp_dir}/perf/control/prerm" >/dev/null
}

check_desktop_deb() {
  local deb
  deb="$(find_deb "${DESKTOP_PACKAGE}")"
  dpkg-deb --field "${deb}" Architecture | grep -Fx all >/dev/null
  extract_deb "${deb}" "${tmp_dir}/desktop"

  test -x "${tmp_dir}/desktop/root${LIB_DIR}/configure-desktop-open.sh"
  test ! -e "${tmp_dir}/desktop/root/usr/bin/xcli"
  test ! -e "${tmp_dir}/desktop/root${LIB_DIR}/configure-timezone.sh"
  test ! -e "${tmp_dir}/desktop/root${LIB_DIR}/enable_performance_mode.sh"
  test ! -d "${tmp_dir}/desktop/root/lib/systemd/system"
  test -x "${tmp_dir}/desktop/control/postinst"
  test -x "${tmp_dir}/desktop/control/prerm"
  test -x "${tmp_dir}/desktop/control/postrm"
  grep -F "configure-desktop-open.sh" "${tmp_dir}/desktop/control/postinst" >/dev/null
  grep -F "configure-desktop-open.sh\" --restore" "${tmp_dir}/desktop/control/prerm" >/dev/null
  if grep -E "systemctl enable|WantedBy=" "${tmp_dir}/desktop/control/postinst" >/dev/null; then
    echo "desktop package must not enable a boot unit" >&2
    exit 1
  fi
}

check_timezone_deb
check_desktop_deb
check_performance_deb

echo "Package content check passed"
