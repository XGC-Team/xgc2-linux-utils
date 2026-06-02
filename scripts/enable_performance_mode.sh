#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=source/ros_ws/src/linux_performance/scripts/common.sh
. "$SCRIPT_DIR/common.sh"

mkdir -p "$STATE_DIR"
save_power_profile
save_current_governors

echo "Saved previous CPU state under: $STATE_DIR"

if have_command powerprofilesctl; then
    echo "Setting power profile to performance..."
    set_power_profile performance || echo "Warning: failed to set power profile to performance." >&2
else
    echo "powerprofilesctl not found; skipping power profile change."
fi

if governor_is_available performance; then
    echo "Setting CPU frequency governor to performance..."
    set_all_governors performance
else
    echo "Warning: performance governor is not available on this system." >&2
fi

echo
echo "Current governor summary:"
print_governor_summary || true
