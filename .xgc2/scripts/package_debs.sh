#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PERF_PACKAGE="xgc2-utils-linux-performance-mode"
TZ_PACKAGE="xgc2-utils-linux-timezone"
ARCHITECTURE="${ARCHITECTURE:-all}"
OUTPUT_DIR=""
LIB_DIR="/usr/lib/xgc2-utils/linux"

usage() {
  cat <<EOF
Usage: ${0##*/} --output-dir DIR

Build the ${TZ_PACKAGE} and ${PERF_PACKAGE} Debian packages.
EOF
}

product_version() {
  awk -F': *' '/^version:[[:space:]]*/ {print $2; exit}' "${REPO_ROOT}/.xgc2/product.yml"
}

VERSION="${PACKAGE_VERSION:-$(product_version)}"
VERSION="${VERSION:-1.1.0-1}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir)
      OUTPUT_DIR="$2"
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

if [[ -z "${OUTPUT_DIR}" ]]; then
  echo "--output-dir is required" >&2
  usage >&2
  exit 2
fi

BUILD_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${BUILD_DIR}"
}
trap cleanup EXIT

mkdir -p "${OUTPUT_DIR}"
rm -f "${OUTPUT_DIR}"/*.deb

write_control() {
  local pkg_root="$1"
  local package="$2"
  local depends="$3"
  local recommends="$4"
  local description="$5"
  local long_description="$6"
  local installed_size
  installed_size="$(du -sk "${pkg_root}" | awk '{print $1}')"
  cat > "${pkg_root}/DEBIAN/control" <<EOF
Package: ${package}
Version: ${VERSION}
Section: admin
Priority: optional
Architecture: ${ARCHITECTURE}
Installed-Size: ${installed_size}
Maintainer: XGC2 <apt@example.com>
Depends: ${depends}
Recommends: ${recommends}
Description: ${description}
 ${long_description}
EOF
  chmod 0644 "${pkg_root}/DEBIAN/control"
}

build_timezone_deb() {
  local pkg_root="${BUILD_DIR}/${TZ_PACKAGE}"
  install -d \
    "${pkg_root}/DEBIAN" \
    "${pkg_root}${LIB_DIR}" \
    "${pkg_root}/usr/share/doc/${TZ_PACKAGE}"

  install -m 0755 \
    "${REPO_ROOT}/scripts/configure-timezone.sh" \
    "${pkg_root}${LIB_DIR}/configure-timezone.sh"
  install -m 0755 \
    "${REPO_ROOT}/scripts/configure-time-sync.sh" \
    "${pkg_root}${LIB_DIR}/configure-time-sync.sh"
  install -m 0755 \
    "${REPO_ROOT}/.xgc2/debian/${TZ_PACKAGE}/postinst" \
    "${pkg_root}/DEBIAN/postinst"
  install -m 0755 \
    "${REPO_ROOT}/.xgc2/debian/${TZ_PACKAGE}/prerm" \
    "${pkg_root}/DEBIAN/prerm"
  install -m 0755 \
    "${REPO_ROOT}/.xgc2/debian/${TZ_PACKAGE}/postrm" \
    "${pkg_root}/DEBIAN/postrm"

  cat > "${pkg_root}/usr/share/doc/${TZ_PACKAGE}/README" <<EOF
${TZ_PACKAGE}

Install-once host timezone package for every XGC2 robot.

On configure it writes Asia/Shanghai and enables systemd-timesyncd.
It does not ship a boot service. The timezone is a persistent OS
setting; NTP is the distro timesyncd unit, enabled once.

Remove the package to restore the previous zone and NTP state.
EOF
  chmod 0644 "${pkg_root}/usr/share/doc/${TZ_PACKAGE}/README"

  write_control \
    "${pkg_root}" \
    "${TZ_PACKAGE}" \
    "tzdata, bash, coreutils" \
    "systemd" \
    "XGC2 robot timezone Asia/Shanghai" \
    "Sets Asia/Shanghai once on install and enables NTP. No boot unit."

  dpkg-deb --root-owner-group --build \
    "${pkg_root}" \
    "${OUTPUT_DIR}/${TZ_PACKAGE}_${VERSION}_${ARCHITECTURE}.deb" >/dev/null
}

build_performance_deb() {
  local pkg_root="${BUILD_DIR}/${PERF_PACKAGE}"
  local share_dir="/usr/share/${PERF_PACKAGE}"
  install -d \
    "${pkg_root}/DEBIAN" \
    "${pkg_root}${LIB_DIR}" \
    "${pkg_root}${share_dir}" \
    "${pkg_root}/usr/bin" \
    "${pkg_root}/usr/share/bash-completion/completions" \
    "${pkg_root}/etc/bash_completion.d" \
    "${pkg_root}/usr/share/doc/${PERF_PACKAGE}"

  local script
  for script in "${REPO_ROOT}"/scripts/*.sh; do
    local base
    base="$(basename "${script}")"
    case "${base}" in
      configure-timezone.sh|configure-time-sync.sh)
        continue
        ;;
    esac
    local mode=0755
    if [[ "${base}" == "common.sh" ]]; then
      mode=0644
    fi
    install -m "${mode}" "${script}" "${pkg_root}${LIB_DIR}/${base}"
  done

  install -m 0755 "${REPO_ROOT}/scripts/xcli" "${pkg_root}${LIB_DIR}/xcli"
  ln -sfn "${LIB_DIR}/xcli" "${pkg_root}/usr/bin/xcli"

  local py
  for py in "${REPO_ROOT}"/scripts/xcli_eval.py "${REPO_ROOT}"/scripts/xcli_eval_*.py; do
    [[ -f "${py}" ]] || continue
    local base
    base="$(basename "${py}")"
    if [[ "${base}" == "xcli_eval.py" ]]; then
      install -m 0755 "${py}" "${pkg_root}${LIB_DIR}/${base}"
    else
      install -m 0644 "${py}" "${pkg_root}${LIB_DIR}/${base}"
    fi
  done
  if [[ -f "${REPO_ROOT}/scripts/xcli-complete.bash" ]]; then
    install -m 0644 "${REPO_ROOT}/scripts/xcli-complete.bash" \
      "${pkg_root}/usr/share/bash-completion/completions/xcli"
    install -m 0644 "${REPO_ROOT}/scripts/xcli-complete.bash" \
      "${pkg_root}/etc/bash_completion.d/xcli"
  fi

  install -m 0644 \
    "${REPO_ROOT}/config/cpufrequtils.default" \
    "${pkg_root}${share_dir}/cpufrequtils.default"

  install -m 0755 "${REPO_ROOT}/.xgc2/debian/postinst" "${pkg_root}/DEBIAN/postinst"
  install -m 0755 "${REPO_ROOT}/.xgc2/debian/prerm" "${pkg_root}/DEBIAN/prerm"
  install -m 0755 "${REPO_ROOT}/.xgc2/debian/postrm" "${pkg_root}/DEBIAN/postrm"

  cat > "${pkg_root}/usr/share/doc/${PERF_PACKAGE}/README" <<EOF
${PERF_PACKAGE}

Installs XGC2 Linux helper scripts under ${LIB_DIR} and enables
cpufrequtils performance governor persistence.

Timezone and NTP live in ${TZ_PACKAGE}.
EOF
  chmod 0644 "${pkg_root}/usr/share/doc/${PERF_PACKAGE}/README"

  write_control \
    "${pkg_root}" \
    "${PERF_PACKAGE}" \
    "cpufrequtils, bash, coreutils, procps, python3, ${TZ_PACKAGE} (>= ${VERSION})" \
    "iproute2, iptables, network-manager" \
    "XGC2 Linux host utilities and xcli" \
    "Installs shared xcli/host scripts. Persists the CPU governor as performance."

  dpkg-deb --root-owner-group --build \
    "${pkg_root}" \
    "${OUTPUT_DIR}/${PERF_PACKAGE}_${VERSION}_${ARCHITECTURE}.deb" >/dev/null
}

build_timezone_deb
build_performance_deb

find "${OUTPUT_DIR}" -maxdepth 1 -type f -name '*.deb' -print | sort
