OmniSim Husky bag (rosbag2, sqlite3 storage)

Source:    OmniSim simulator, commit 79aadbe38, simulated Clearpath Husky with a
           541-beam LaserScan, IMU and GPS, bridged to ROS 2.
Stack:     ROS 2 Humble, Fast DDS (rmw_fastrtps_cpp), rosbag2 0.15.16.
Recorded:  2026-10-02, 35.36 s, `ros2 bag record -a`, by the OmniSim team, who
           allowed its use as a test fixture.
Contents:  1434 messages on 11 topics: /scan 177, /odom 177, /imu/data 177,
           /gps/local 177, /clock 353, /tf 266, /husky/joint_states 89,
           /rosout 13, /parameter_events 3, /tf_static 2, /events/write_split 0.
           The robot was not driven; walls stand at known positions.
Format:    metadata version 5 (Humble). It embeds no message definitions, so
           reading it needs the Humble type definitions, which makes it the
           regression case for `peek_bag_samples` on Humble bags.
sha256 of omnisim_husky_topicforge_20261002_190546_0.db3:
           51ab2b34d3873a86b30c70880f588b91f600c3b209f01b92e3ddedf8210daa2f

ros2_bag_info.txt is the stdout of `ros2 bag info` on this bag, as captured by
the OmniSim team.
