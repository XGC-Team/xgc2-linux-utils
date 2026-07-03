#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PACKAGE="${PACKAGE:-xgc2-utils-linux-performance-mode}"
ARCHITECTURE="${ARCHITECTURE:-all}"
OUTPUT_DIR=""
LIB_DIR="/usr/lib/xgc2-utils/linux"
SHARE_DIR="/usr/share/${PACKAGE}"

usage() {
  cat <<EOF
Usage: ${0##*/} --output-dir DIR

Build the ${PACKAGE} Debian package from this repository.
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

pkg_root="${BUILD_DIR}/${PACKAGE}"
install -d \
  "${pkg_root}/DEBIAN" \
  "${pkg_root}${LIB_DIR}" \
  "${pkg_root}${SHARE_DIR}" \
  "${pkg_root}/usr/share/doc/${PACKAGE}"

for script in "${REPO_ROOT}"/scripts/*.sh; do
  mode=0755
  if [[ "$(basename "${script}")" == "common.sh" ]]; then
    mode=0644
  fi
  install -m "${mode}" "${script}" "${pkg_root}${LIB_DIR}/$(basename "${script}")"
done

install -m 0644 \
  "${REPO_ROOT}/config/cpufrequtils.default" \
  "${pkg_root}${SHARE_DIR}/cpufrequtils.default"

install -m 0755 "${REPO_ROOT}/.xgc2/debian/postinst" "${pkg_root}/DEBIAN/postinst"
install -m 0755 "${REPO_ROOT}/.xgc2/debian/prerm" "${pkg_root}/DEBIAN/prerm"
install -m 0755 "${REPO_ROOT}/.xgc2/debian/postrm" "${pkg_root}/DEBIAN/postrm"

cat > "${pkg_root}/usr/share/doc/${PACKAGE}/README" <<EOF
${PACKAGE}

Installs XGC2 Linux helper scripts under ${LIB_DIR} and enables
cpufrequtils performance governor persistence.
EOF
chmod 0644 "${pkg_root}/usr/share/doc/${PACKAGE}/README"

installed_size="$(du -sk "${pkg_root}" | awk '{print $1}')"
cat > "${pkg_root}/DEBIAN/control" <<EOF
Package: ${PACKAGE}
Version: ${VERSION}
Section: admin
Priority: optional
Architecture: ${ARCHITECTURE}
Installed-Size: ${installed_size}
Maintainer: XGC2 <apt@example.com>
Depends: cpufrequtils, bash, coreutils, procps
Recommends: iproute2, iptables
Description: XGC2 Linux performance mode configuration
 Installs XGC2 Linux utility scripts and configures cpufrequtils to persist
 the host CPU governor as performance across boots.
EOF
chmod 0644 "${pkg_root}/DEBIAN/control"

dpkg-deb --root-owner-group --build \
  "${pkg_root}" \
  "${OUTPUT_DIR}/${PACKAGE}_${VERSION}_${ARCHITECTURE}.deb" >/dev/null

find "${OUTPUT_DIR}" -maxdepth 1 -type f -name '*.deb' -print | sort
