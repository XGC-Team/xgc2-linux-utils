#!/usr/bin/env bash
set -euo pipefail

PACKAGE="${PACKAGE:-xgc2-utils-linux-performance-mode}"
LIB_DIR="/usr/lib/xgc2-utils/linux"
SHARE_DIR="/usr/share/${PACKAGE}"
DEB_PATH=""

usage() {
  cat <<EOF
Usage: ${0##*/} --deb PATH

Validate package metadata and payload without installing the package.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
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

if [[ -z "${DEB_PATH}" ]]; then
  echo "--deb is required" >&2
  usage >&2
  exit 2
fi

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "${tmp_dir}"
}
trap cleanup EXIT

dpkg-deb --field "${DEB_PATH}" Package | grep -Fx "${PACKAGE}" >/dev/null
dpkg-deb --field "${DEB_PATH}" Architecture | grep -Fx all >/dev/null
dpkg-deb --field "${DEB_PATH}" Depends | grep -F "cpufrequtils" >/dev/null

dpkg-deb --extract "${DEB_PATH}" "${tmp_dir}/root"
dpkg-deb --control "${DEB_PATH}" "${tmp_dir}/control"

test -r "${tmp_dir}/root${LIB_DIR}/common.sh"
test -x "${tmp_dir}/root${LIB_DIR}/xcli"
test -L "${tmp_dir}/root/usr/bin/xcli"
test -r "${tmp_dir}/root${LIB_DIR}/xcli_eval.py"
test -r "${tmp_dir}/root${LIB_DIR}/xcli_eval_host.py"
test -r "${tmp_dir}/root${LIB_DIR}/xcli_eval_ros.py"
test -r "${tmp_dir}/root/usr/share/bash-completion/completions/xcli"
for script in \
  configure-display-idle.sh \
  configure-log-limits.sh \
  configure-no-suspend.sh \
  configure-time-sync.sh \
  configure-timezone.sh \
  enable_performance_mode.sh \
  print_cpu_frequency.sh \
  query_cpu_state.sh \
  restore-runtime-gateway.sh \
  restore-runtime-nat-gateway.sh \
  restore_balanced_mode.sh \
  setup-runtime-nat-gateway.sh \
  use-runtime-gateway.sh; do
  test -x "${tmp_dir}/root${LIB_DIR}/${script}"
done

test -r "${tmp_dir}/root${SHARE_DIR}/cpufrequtils.default"
grep -Fx 'GOVERNOR="performance"' "${tmp_dir}/root${SHARE_DIR}/cpufrequtils.default" >/dev/null
if find "${tmp_dir}/root/lib/systemd/system" -maxdepth 1 -name 'xgc2-utils-linux-performance-mode.service' 2>/dev/null | grep -q .; then
  echo "package must not ship custom performance-mode systemd service" >&2
  exit 1
fi

test -x "${tmp_dir}/control/postinst"
test -x "${tmp_dir}/control/prerm"
test -x "${tmp_dir}/control/postrm"
grep -F "systemctl_quiet disable ondemand.service" "${tmp_dir}/control/postinst" >/dev/null
grep -F "systemctl_quiet enable cpufrequtils.service" "${tmp_dir}/control/postinst" >/dev/null
grep -F 'GOVERNOR="performance"' "${tmp_dir}/control/postinst" >/dev/null
grep -F "systemctl_quiet stop cpufrequtils.service" "${tmp_dir}/control/prerm" >/dev/null
grep -F "systemctl_quiet enable ondemand.service" "${tmp_dir}/control/prerm" >/dev/null

echo "Package content check passed"
