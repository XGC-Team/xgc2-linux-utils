#!/usr/bin/env sh

STATE_DIR="${DMPC_CPU_STATE_DIR:-/tmp/dmpc_linux_performance_state}"
GOVERNOR_STATE_FILE="$STATE_DIR/governors.state"
PROFILE_STATE_FILE="$STATE_DIR/power_profile.state"

have_command() {
    command -v "$1" >/dev/null 2>&1
}

first_governor_file() {
    for file in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor; do
        [ -e "$file" ] || continue
        printf '%s\n' "$file"
        return 0
    done
    return 1
}

governor_files() {
    for file in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor; do
        [ -e "$file" ] || continue
        printf '%s\n' "$file"
    done
}

print_governor_summary() {
    if ! first_governor_file >/dev/null 2>&1; then
        echo "No CPU frequency governor files found."
        return 1
    fi

    governor_files | while IFS= read -r file; do
        cat "$file"
    done | sort | uniq -c
}

save_power_profile() {
    mkdir -p "$STATE_DIR"
    if have_command powerprofilesctl; then
        powerprofilesctl get >"$PROFILE_STATE_FILE" 2>/dev/null || true
    fi
}

save_current_governors() {
    mkdir -p "$STATE_DIR"
    : >"$GOVERNOR_STATE_FILE"

    governor_files | while IFS= read -r file; do
        governor=$(cat "$file" 2>/dev/null || true)
        [ -n "$governor" ] || continue
        printf '%s %s\n' "$file" "$governor" >>"$GOVERNOR_STATE_FILE"
    done
}

set_power_profile() {
    profile=$1
    have_command powerprofilesctl || return 1

    if powerprofilesctl set "$profile"; then
        return 0
    fi

    if have_command sudo; then
        sudo powerprofilesctl set "$profile"
        return $?
    fi

    return 1
}

write_sysfs_value() {
    value=$1
    path=$2

    if [ "$(id -u)" -eq 0 ]; then
        printf '%s\n' "$value" >"$path"
    else
        printf '%s\n' "$value" | sudo tee "$path" >/dev/null
    fi
}

set_all_governors() {
    governor=$1

    if have_command cpupower; then
        if [ "$(id -u)" -eq 0 ]; then
            if cpupower frequency-set -g "$governor"; then
                return 0
            fi
        elif have_command sudo && sudo cpupower frequency-set -g "$governor"; then
            return 0
        fi
        echo "cpupower failed; trying direct sysfs governor writes." >&2
    fi

    found=0
    failed=0
    for file in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor; do
        [ -e "$file" ] || continue
        found=1
        if ! write_sysfs_value "$governor" "$file"; then
            failed=1
        fi
    done

    if [ "$found" -eq 0 ]; then
        echo "No CPU frequency governor files found." >&2
        return 1
    fi

    [ "$failed" -eq 0 ]
}

governor_is_available() {
    governor=$1
    file=$(first_governor_file 2>/dev/null || true)
    [ -n "$file" ] || return 1

    available_file=$(dirname "$file")/scaling_available_governors
    [ -r "$available_file" ] || return 0

    available=$(cat "$available_file")
    case " $available " in
        *" $governor "*) return 0 ;;
        *) return 1 ;;
    esac
}

pick_balanced_governor() {
    file=$(first_governor_file 2>/dev/null || true)
    if [ -n "$file" ]; then
        available_file=$(dirname "$file")/scaling_available_governors
        if [ -r "$available_file" ]; then
            available=$(cat "$available_file")
            for candidate in schedutil ondemand conservative powersave; do
                case " $available " in
                    *" $candidate "*)
                        echo "$candidate"
                        return 0
                        ;;
                esac
            done
        fi
    fi

    echo "powersave"
}

restore_saved_governors() {
    [ -s "$GOVERNOR_STATE_FILE" ] || return 1

    restored=0
    failed=0
    while read -r file governor; do
        [ -n "$file" ] || continue
        [ -n "$governor" ] || continue

        if [ ! -e "$file" ]; then
            echo "Skip missing governor path: $file" >&2
            continue
        fi

        if write_sysfs_value "$governor" "$file"; then
            restored=1
        else
            failed=1
        fi
    done <"$GOVERNOR_STATE_FILE"

    [ "$restored" -eq 1 ] || return 1
    [ "$failed" -eq 0 ]
}
