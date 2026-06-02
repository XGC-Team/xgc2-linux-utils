# linux_performance

ROS1 package with small Linux CPU state helpers for MPC or ROS simulation runs.

`rosrun` can execute shell scripts as long as they have:

- a valid shebang, for example `#!/usr/bin/env sh`
- executable permission
- a package path visible through the sourced ROS workspace

## Usage

After building or sourcing the workspace:

```sh
source source/ros_ws/devel/setup.bash

rosrun linux_performance query_cpu_state.sh
rosrun linux_performance print_cpu_frequency.sh
rosrun linux_performance print_cpu_frequency.sh 1

rosrun linux_performance enable_performance_mode.sh

# Run simulation here.

rosrun linux_performance restore_balanced_mode.sh
```

The state file is stored in `/tmp/dmpc_linux_performance_state` by default. Use
`DMPC_CPU_STATE_DIR=/path/to/state` if you want another location.
