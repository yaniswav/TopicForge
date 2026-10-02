# Round 2 ground truth

crash_live (84): safety_monitor killed at t+25 s. estop loses its only writer, cmd_vel loses a reader.
restart_loop (85): nav_planner crash-restarted at t+15, 23, 31 s (3 restarts, 4 lives).
partition_split (87): camera_driver writes image in partition "front"; front_viewer reads in "rear"
  (never matches, cause = partition; its Reliability difference is irrelevant); recorder in "front" works.
hung_estop (88): estop_publisher stops writing/asserting at ~t+20 s but its process stays alive.
  motor_controller's reader (manual_topic, 500 ms) loses liveliness; participant stays "active".
big_bus (86), seeded problems:
  1. scan: nav_planner RELIABLE reader vs lidar_front BEST_EFFORT writer (Reliability)
  2. mission: mission_control VOLATILE writer vs nav_planner TRANSIENT_LOCAL reader (Durability)
  3. cmd_vel: motor_controller requests deadline 100 ms, nav_planner offers none (Deadline)
  4. estop: motor_controller requests manual_topic liveliness lease 500 ms, safety_monitor offers automatic infinite (Liveliness)
  5. batery typo: dashboard reads "batery" (no writer); battery_monitor's "battery" has no reader
  6. image: camera_driver in partition "front", object_detector in "perception" (Partition, no match)
  7. arm_cmd: arm_controller_a EXCLUSIVE ownership writer vs arm_driver SHARED reader (Ownership)
  controls (must NOT be reported): imu (10 ms offered / 50 ms requested), odom (reliable -> best_effort logger),
  pose, scan_rear (Dust writer -> logger), detections, motor_state.

healthy_bus (89): no problem at all. Mixed Cyclone + Dust; partitions consistent; deadlines compatible
  (imu 10 offered/50 requested, cmd_vel 100 offered/200 requested); mission TRANSIENT_LOCAL both sides.
  Acceptable remarks: Dust participant unnamed/vendor unknown; heartbeat reliable->best_effort is compatible.
partition_wild (90): camera_driver in "robot1". viewer_all ("robot*") receives; viewer_robot2 ("robot2")
  and viewer_default (default partition "") do not.
ownership_pair (91): two EXCLUSIVE writers (strength 10 and 5); arm_driver EXCLUSIVE matches both and
  receives from the strongest live one (arm_controller_a); arm_monitor SHARED reader is incompatible
  (Ownership) and receives nothing. Which writer "owns" at runtime is not observable from discovery.
type_divergence (92): lidar_driver writes scan as LidarScan; localization (Dust) reads scan as Odom:
  type names differ, never matches. nav_planner (LidarScan) works.
domain_confusion (93): imu_driver runs on domain 1, TopicForge watches 93: it cannot be seen;
  localization's imu reader has no writer on 93.
