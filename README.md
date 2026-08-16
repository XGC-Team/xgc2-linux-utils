# XGC2 Linux Utils

This product no longer publishes APT packages.

Timezone, CPU governor, screen idle, autologin, and sleep are host policy.
Operators change them in Core/Agent **System → Host**.
The station applies native Linux interfaces (`timedatectl`, sysfs governors,
dconf/display-manager drop-ins, systemd-logind). It does not install
`xgc2-utils-linux-*` and does not wrap `xcli`.

Scripts in `scripts/` remain as local references only. Do not package them.

```sh
# Retired — these commands must not be used on new machines:
# sudo apt install xgc2-utils-linux-timezone
# sudo apt install xgc2-utils-linux-desktop
# sudo apt install xgc2-utils-linux-performance-mode
```
