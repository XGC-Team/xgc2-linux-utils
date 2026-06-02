cmake_minimum_required(VERSION 3.0.2)
project(linux_performance)

find_package(catkin REQUIRED)

catkin_package()

catkin_install_python(PROGRAMS
  scripts/enable_performance_mode.sh
  scripts/print_cpu_frequency.sh
  scripts/query_cpu_state.sh
  scripts/restore_balanced_mode.sh
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)

install(FILES
  scripts/common.sh
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)
