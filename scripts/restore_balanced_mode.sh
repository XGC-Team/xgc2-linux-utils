#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=source/ros_ws/src/linux_performance/scripts/common.sh
. "$SCRIPT_DIR/common.sh"

if have_command powerprofilesctl; then
    profile=balanced
    if [ -s "$PROFILE_STATE_FILE" ]; then
        profile=$(cat "$PROFILE_STATE_FILE")
    fi

    echo "Setting power profile to: $profile"
    set_power_profile "$profile" || echo "Warning: failed to set power profile to $profile." >&2
else
    echo "powerprofilesctl not found; skipping power profile restore."
fi

if [ -s "$GOVERNOR_STATE_FILE" ]; then
    echo "Restoring saved CPU frequency governors..."
    restore_saved_governors || {
        echo "Warning: saved governor restore failed; using balanced fallback." >&2
        fallback=$(pick_balanced_governor)
        set_all_governors "$fallback"
    }
else
    fallback=$(pick_balanced_governor)
    echo "No saved governor state found; setting governor to: $fallback"
    set_all_governors "$fallback"
fi

echo
echo "Current governor summary:"
print_governor_summary || true
