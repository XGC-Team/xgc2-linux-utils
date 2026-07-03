#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=scripts/common.sh
. "$SCRIPT_DIR/common.sh"

echo "time: $(date '+%F %T %Z')"
echo "kernel: $(uname -srmo)"
echo

if [ -r /sys/devices/system/cpu/online ]; then
    echo "online_cpus: $(cat /sys/devices/system/cpu/online)"
fi

if have_command nproc; then
    echo "logical_cpus: $(nproc --all)"
fi

if have_command powerprofilesctl; then
    echo "power_profile: $(powerprofilesctl get 2>/dev/null || echo unavailable)"
else
    echo "power_profile: powerprofilesctl not installed"
fi

governor_file=$(first_governor_file 2>/dev/null || true)
if [ -n "$governor_file" ]; then
    cpu_dir=$(dirname "$governor_file")

    [ -r "$cpu_dir/scaling_driver" ] && echo "scaling_driver: $(cat "$cpu_dir/scaling_driver")"
    [ -r "$cpu_dir/scaling_available_governors" ] && echo "available_governors: $(cat "$cpu_dir/scaling_available_governors")"
    [ -r "$cpu_dir/scaling_min_freq" ] && echo "policy_min_khz: $(cat "$cpu_dir/scaling_min_freq")"
    [ -r "$cpu_dir/scaling_max_freq" ] && echo "policy_max_khz: $(cat "$cpu_dir/scaling_max_freq")"

    echo "governor_summary:"
    print_governor_summary
else
    echo "governor_summary: unavailable"
fi

if [ -r /sys/devices/system/cpu/intel_pstate/no_turbo ]; then
    no_turbo=$(cat /sys/devices/system/cpu/intel_pstate/no_turbo)
    if [ "$no_turbo" = "0" ]; then
        echo "intel_turbo: enabled"
    else
        echo "intel_turbo: disabled"
    fi
fi

if [ -r /sys/devices/system/cpu/cpufreq/boost ]; then
    boost=$(cat /sys/devices/system/cpu/cpufreq/boost)
    if [ "$boost" = "1" ]; then
        echo "cpu_boost: enabled"
    else
        echo "cpu_boost: disabled"
    fi
fi

if have_command sensors; then
    echo
    echo "temperature_snapshot:"
    sensors 2>/dev/null | sed -n '1,24p' || true
fi
