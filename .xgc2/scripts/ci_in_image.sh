#!/usr/bin/env bash
# Build and smoke-test this product inside an XGC2 CI image.
# The container is offline. Extra packages belong in xgc2-images.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

DOCKER_IMAGE=""
OUTPUT_DIR="${REPO_ROOT}/debs"

usage() {
  cat <<EOF
Usage: ${0##*/} --image IMAGE [--output-dir DIR]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image)
      DOCKER_IMAGE="$2"
      shift 2
      ;;
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

if [[ -z "${DOCKER_IMAGE}" ]]; then
  echo "--image is required" >&2
  exit 2
fi

mkdir -p "${OUTPUT_DIR}"
docker pull "${DOCKER_IMAGE}"

docker run --rm --network none \
  -e DEBIAN_FRONTEND=noninteractive \
  -v "${REPO_ROOT}:/workspace/src:ro" \
  -v "${OUTPUT_DIR}:/workspace/out" \
  "${DOCKER_IMAGE}" \
  bash -lc '
    set -euo pipefail
    command -v python3 >/dev/null
    command -v dpkg-deb >/dev/null
    command -v bash >/dev/null
    bash -n /workspace/src/scripts/xcli
    bash -n /workspace/src/scripts/configure-timezone.sh
    bash -n /workspace/src/scripts/configure-time-sync.sh
    bash -n /workspace/src/.xgc2/scripts/package_debs.sh
    bash -n /workspace/src/.xgc2/scripts/check_installed_packages.sh
    bash -n /workspace/src/.xgc2/debian/postinst
    bash -n /workspace/src/.xgc2/debian/prerm
    bash -n /workspace/src/.xgc2/debian/postrm
    bash -n /workspace/src/.xgc2/debian/xgc2-utils-linux-timezone/postinst
    bash -n /workspace/src/.xgc2/debian/xgc2-utils-linux-timezone/prerm
    bash -n /workspace/src/.xgc2/debian/xgc2-utils-linux-timezone/postrm
    python3 - <<'PY'
import ast
from pathlib import Path
root = Path("/workspace/src/scripts")
for name in (
    "xcli_eval.py",
    "xcli_eval_host.py",
    "xcli_eval_ros.py",
    "xcli_eval_mav.py",
):
    path = root / name
    ast.parse(path.read_text(), filename=str(path))
    print("syntax ok", name)
PY
    /workspace/src/.xgc2/scripts/package_debs.sh --output-dir /workspace/out
    shopt -s nullglob
    built_debs=(/workspace/out/*.deb)
    shopt -u nullglob
    if [[ "${#built_debs[@]}" -ne 2 ]]; then
      echo "expected 2 linux-utils debs, found ${#built_debs[@]}" >&2
      ls -la /workspace/out >&2 || true
      exit 1
    fi
    /workspace/src/.xgc2/scripts/check_installed_packages.sh --deb-dir /workspace/out
    export XGC2_LINUX_UTILS=/workspace/src/scripts
    /workspace/src/scripts/xcli help >/tmp/xcli.help
    grep -q "^NAME$" /tmp/xcli.help
    /workspace/src/scripts/xcli eval --once >/tmp/xcli.once
    grep -q "^host=" /tmp/xcli.once
    echo "ci_in_image ok $(python3 --version) $(uname -m)"
  '
