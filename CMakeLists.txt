cmake_minimum_required(VERSION 3.0.2)
project(linux_performance)

find_package(catkin REQUIRED)

catkin_package()

catkin_install_python(PROGRAMS
  scripts/configure-log-limits.sh
  scripts/enable_performance_mode.sh
  scripts/print_cpu_frequency.sh
  scripts/query_cpu_state.sh
  scripts/restore-runtime-gateway.sh
  scripts/restore-runtime-nat-gateway.sh
  scripts/restore_balanced_mode.sh
  scripts/setup-runtime-nat-gateway.sh
  scripts/use-runtime-gateway.sh
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)

install(FILES
  scripts/common.sh
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)
