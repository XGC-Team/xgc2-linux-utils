#!/usr/bin/env bash
# Host timezone, desktop, idle, and CPU governor are Core/Agent System
# policy. These debs are no longer built or published.
set -euo pipefail
echo "xgc2-utils-linux-* packages are retired." >&2
echo "Change timezone, CPU governor, screen idle, autologin, and sleep from the System Overview host policy panel." >&2
exit 1
