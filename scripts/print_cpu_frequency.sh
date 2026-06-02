#!/usr/bin/env sh
set -eu

print_once() {
    echo "time: $(date '+%F %T %Z')"
    awk -F: '
        /cpu MHz/ {
            value=$2
            gsub(/^[ \t]+/, "", value)
            mhz = value + 0
            count++
            freq[count] = mhz
            sum += mhz
            if (count == 1 || mhz < min) min = mhz
            if (count == 1 || mhz > max) max = mhz
        }
        END {
            if (count == 0) {
                print "cpu_frequency: unavailable"
                exit 1
            }
            printf "logical_cpus: %d\n", count
            printf "min_mhz: %.1f\n", min
            printf "avg_mhz: %.1f\n", sum / count
            printf "max_mhz: %.1f\n", max
            for (i = 1; i <= count; i++) {
                printf "cpu%-3d %8.1f MHz\n", i - 1, freq[i]
            }
        }
    ' /proc/cpuinfo
}

interval=${1:-}

if [ -z "$interval" ]; then
    print_once
    exit 0
fi

case "$interval" in
    ''|*[!0-9.]*)
        echo "Usage: $0 [interval_seconds]" >&2
        exit 2
        ;;
esac

while :; do
    print_once
    sleep "$interval"
    echo
done
