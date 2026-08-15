# XGC2 Linux Utils

Host-level Linux utility scripts for XGC2 runtime machines.

## Package

- Product id: `xgc2-linux-utils`
- Source path: `products/utils/linux`
- Release branch: `main`
- Package type: `toolchain-apt`
- Published packages:
  - `xgc2-utils-linux-timezone` — every robot. Sets `Asia/Shanghai` once.
  - `xgc2-utils-linux-performance-mode` — xcli plus CPU performance persistence
- Runtime service:
  - `cpufrequtils.service` (performance-mode only)

## Timezone

`xgc2-utils-linux-timezone` applies on **install**, not on every boot.

- `postinst` writes `Asia/Shanghai` and enables the distro `systemd-timesyncd`.
- There is no XGC2 timezone boot unit. `/etc/localtime` persists across reboot.
- NTP is the stock timesyncd service, enabled once. When a network appears,
  the clock syncs; this package does not wait for a network during `apt`.
- `apt remove` restores the previous zone and NTP state.

```sh
sudo apt update
sudo apt install xgc2-utils-linux-timezone
timedatectl
```

## Install

Every robot should install the timezone package. Add performance-mode only
when the host should persist the CPU governor and ship `xcli`.

```sh
sudo apt update
sudo apt install xgc2-utils-linux-timezone
sudo apt install xgc2-utils-linux-performance-mode
```

Installing performance-mode writes `/etc/default/cpufrequtils`, disables Ubuntu's
default `ondemand.service`, and enables `cpufrequtils.service` so the CPU
governor is restored to `performance` at boot. It also installs `/usr/bin/xcli`
and depends on the timezone package.

## Smoke Test

```sh
/usr/lib/xgc2-utils/linux/query_cpu_state.sh
/usr/lib/xgc2-utils/linux/print_cpu_frequency.sh
cat /etc/default/cpufrequtils
systemctl status cpufrequtils.service
```

## Utility Scripts

Use `xcli` on the robot. Two interfaces share the same verbs:

- **CLI** for agents and scripts: `xcli <domain> <verb>`. Mutating verbs
  save prior state and can be reversed with `restore`.
- **TUI** for operators: `xcli` or `xcli eval`. Overview of disk, top
  processes, NIC rates, and ROS topic Hz / jitter / pub-sub. `m` opens
  the configuration menu (timezone, Wi-Fi, CPU, screen, sleep). Agents
  that cannot attach a tty should use `xcli eval --once`.

```sh
xcli help
xcli eval --once
xcli wifi connect LabNet secret
xcli wifi restore
xcli time zone shanghai
xcli time restore
xcli screen idle 1800
xcli screen restore
xcli sleep off
xcli sleep on
xcli cpu performance
xcli cpu balanced
```

Installed scripts live under `/usr/lib/xgc2-utils/linux`. The timezone
package owns `configure-timezone.sh` and `configure-time-sync.sh`.
Performance-mode owns xcli and the CPU/network helpers.

- `configure-timezone.sh` (timezone package)
- `configure-time-sync.sh` (timezone package)
- `configure-display-idle.sh`
- `configure-no-suspend.sh`
- `configure-log-limits.sh`
- `enable_performance_mode.sh`
- `restore_balanced_mode.sh`
- `query_cpu_state.sh`
- `print_cpu_frequency.sh`
- `setup-runtime-nat-gateway.sh`
- `restore-runtime-nat-gateway.sh`
- `use-runtime-gateway.sh`
- `restore-runtime-gateway.sh`

The package stores a backup of any previous `/etc/default/cpufrequtils` under
`/var/lib/xgc2-utils-linux-performance-mode` and restores it on removal. Manual
runs of the helper scripts use `/tmp/dmpc_linux_performance_state` by default
unless `DMPC_CPU_STATE_DIR` is set.

## Build

```sh
.xgc2/scripts/package_debs.sh --output-dir debs
.xgc2/scripts/check_installed_packages.sh --deb debs/*.deb
```
