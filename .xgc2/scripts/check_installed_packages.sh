#!/usr/bin/env bash
set -euo pipefail

ROS_DISTRO="${ROS_DISTRO:-noetic}"
ROS_PACKAGE="linux_performance"

source "/opt/ros/${ROS_DISTRO}/setup.bash"

dpkg -s ros-noetic-xgc2-linux-utils >/dev/null
test "$(rospack find "${ROS_PACKAGE}")" = "/opt/ros/${ROS_DISTRO}/share/${ROS_PACKAGE}"

for script in \
  configure-log-limits.sh \
  enable_performance_mode.sh \
  print_cpu_frequency.sh \
  query_cpu_state.sh \
  restore-runtime-gateway.sh \
  restore-runtime-nat-gateway.sh \
  restore_balanced_mode.sh \
  setup-runtime-nat-gateway.sh \
  use-runtime-gateway.sh; do
  path="/opt/ros/${ROS_DISTRO}/lib/${ROS_PACKAGE}/${script}"
  test -x "${path}"
done

rosrun "${ROS_PACKAGE}" print_cpu_frequency.sh >/dev/null

echo "Installed package check passed"
