# External validation

## OmniSim (October 2026)

The OmniSim team ran TopicForge against a simulated robot and compared its answers with the simulator's ground truth.

Setup: [OmniSim v9.1.3](https://github.com/omnilink-tech/omnisim/releases/tag/v9.1.3), a simulated Clearpath Husky with a 541-beam SICK LMS111 lidar in a room with walls at known positions, robot stationary. ROS 2 Humble on Ubuntu 22.04 (WSL2), Fast DDS. TopicForge 0.5.3 in live mode for the ROS 2 tools, and 0.5.5 with the Cyclone backend for the DDS tools.

What matched:
- Every lidar scan TopicForge sampled was bit-identical to the simulator's raw data on the beams it delivered.
- `list_topics` and `get_topic_info` returned the same names, types and publisher/subscriber counts as the `ros2` CLI.
- `analyze_bag` returned the same duration and message counts as `ros2 bag info` and an independent read of the bag.
- With the Cyclone backend, TopicForge saw all seven Fast DDS participants of the ROS 2 graph without extra configuration, and reported participants leaving within about 10 ms of the actual time.

What it found: seven discrepancies on TopicForge's side, including lidar arrays cut at 128 values, one message per `sample_messages` call, zero timestamps on simulated time, and Humble bags that `peek_bag_samples` could not decode. All seven are addressed in 0.5.6 and 0.6.0 (full arrays through a `max_array_length` option) and covered by a ROS 2 Humble/Jazzy test bench; the OmniSim run itself has not been repeated on these versions yet. The bag recorded during the run is part of TopicForge's test fixtures (`tests/fixtures/bags/omnisim_humble/`).
