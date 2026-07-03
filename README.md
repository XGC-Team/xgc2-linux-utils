# XGC2 Linux Utils

Host-level Linux utility scripts for XGC2 runtime machines.

## Package

- Product id: `xgc2-linux-utils`
- Source path: `products/utils/linux`
- Release branch: `main`
- Package type: `toolchain-apt`
- Published package:
  - `xgc2-utils-linux-performance-mode`
- Runtime service:
  - `cpufrequtils.service`

## Install

```sh
sudo apt update
sudo apt install xgc2-utils-linux-performance-mode
```

Installing the package writes `/etc/default/cpufrequtils`, disables Ubuntu's
default `ondemand.service`, and enables `cpufrequtils.service` so the CPU
governor is restored to `performance` at boot.

## Smoke Test

```sh
/usr/lib/xgc2-utils/linux/query_cpu_state.sh
/usr/lib/xgc2-utils/linux/print_cpu_frequency.sh
cat /etc/default/cpufrequtils
systemctl status cpufrequtils.service
```

## Utility Scripts

Installed scripts live under `/usr/lib/xgc2-utils/linux`:

- `enable_performance_mode.sh`
- `restore_balanced_mode.sh`
- `query_cpu_state.sh`
- `print_cpu_frequency.sh`
- `configure-log-limits.sh`
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
