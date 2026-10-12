# External validation

## OmniSim (October 2026)

The OmniSim team ran TopicForge against a simulated robot and compared its answers with the simulator's ground truth.

Setup: [OmniSim v9.1.3](https://github.com/omnilink-tech/omnisim/releases/tag/v9.1.3), a simulated Clearpath Husky with a 541-beam SICK LMS111 lidar in a room with walls at known positions, robot stationary. ROS 2 Humble on Ubuntu 22.04 (WSL2), Fast DDS. TopicForge 0.5.3 in live mode for the ROS 2 tools, and 0.5.5 with the Cyclone backend for the DDS tools.

What matched:
- Every lidar scan TopicForge sampled was bit-identical to the simulator's raw data on the beams it delivered.
- `list_topics` and `get_topic_info` returned the same names, types and publisher/subscriber counts as the `ros2` CLI.
- `analyze_bag` returned the same duration and message counts as `ros2 bag info` and an independent read of the bag.
- With the Cyclone backend, TopicForge saw all seven Fast DDS participants of the ROS 2 graph without extra configuration, and reported participants leaving within about 10 ms of the actual time.

What it found: seven discrepancies on TopicForge's side, including lidar arrays cut at 128 values, one message per `sample_messages` call, zero timestamps on simulated time, and Humble bags that `peek_bag_samples` could not decode. All seven are addressed in 0.5.6 and 0.6.0 (full arrays through a `max_array_length` option) and covered by a ROS 2 Humble/Jazzy test bench; the run was repeated on 0.6.3 and 0.6.4 with the OmniSim team's ground-truth kit (next section). The bag recorded during the run is part of TopicForge's test fixtures (`tests/fixtures/bags/omnisim_humble/`).

## OmniSim ground-truth kit (5 October 2026 edition, run on 8 October)

After the first report, the OmniSim team built a ground-truth kit: one command brings the same graph back up and has the kit measure the truth itself, then write it to `ground_truth.json`. That covers the expected range of every beam computed from the world geometry, the wall distances, rates in simulated and wall time, the topic list with types and counts as the `ros2` CLI reports them, and `use_sim_time` per node. The kit is distributed by OmniLink (Apache-2.0); the simulator is [OmniSim](https://github.com/omnilink-tech/omnisim).

We ran the kit ourselves, twice, and compared TopicForge's answers with `ground_truth.json` field by field, through the real MCP interface.

Setup, as the kit's README describes it: OmniSim 9.2.0 on Windows, ROS 2 Humble on Ubuntu 22.04 in WSL2 (NAT networking, domain 0, default RMW, i.e. Fast DDS 2.6.12), a simulated Clearpath Husky with a 541-beam SICK LMS111 lidar in a room with walls at known positions, robot stationary, a 35 s bag recorded by the kit. TopicForge ran in live mode inside the same WSL distro, with the Cyclone backend observing the DDS bus. The kit's own self-check reported 26/26 on both runs.

| What was compared | Result |
|---|---|
| Topics: 12 names and types, publisher and subscription counts | identical |
| Reliability and durability of every publisher | identical |
| `/scan`: beam count, angles, range limits, frame | identical (angles bit-identical) |
| `/scan`: all 541 ranges against the published scan | 0 differences (tolerance 1e-5; 362 finite, 179 inf) |
| `/scan` beams facing the three walls against the world geometry | within 0.03 mm |
| Header stamps | simulated time, as published |
| Bag: message count, duration, per-topic counts and rates | identical (rates to 6 digits) |
| `peek_bag_samples` on `/scan` against the published scan | 0 differences |
| DDS: participants seen by the Cyclone observer | the 7 Fast DDS participants (6 nodes and the `ros2` daemon) |
| DDS: QoS mismatch detection on a healthy graph | 0 false positives |

The first run, on TopicForge 0.6.3, found no wrong value but surfaced gaps. Three were fixed in 0.6.4 and checked on the second run the same day: `/clock`, `/tf` and `/rosout` samples now carry their timestamp (`stamp_source` `payload`), `peek_bag_samples` works on a plain install, and the MCP handshake reports TopicForge's version. Since then, 0.7.0 reports QoS for both the publisher and the subscription side and names the RMW in `health_check`. Still open: Fast DDS participants cannot yet be tied to node names, and non-finite ranges are returned as the strings `"inf"`, `"-inf"` and `"nan"`, by design (JSON has no infinity).

Limits, as stated by the kit: one machine, a stationary robot, one room, a noise-free lidar, Fast DDS only, WSL2 in NAT mode. This shows that TopicForge reports what the simulator published, on this setup; it does not cover a moving robot, other DDS vendors on the bus, native Linux or multi-machine networks. Rates were compared on the bag only, not measured live.

## Continuous ground truth

The OmniSim run above is manual and external. A second, automatic producer of ground truth now feeds the same comparator (`scripts/ground_truth/compare.py`). The ROS 2 Docker bench publisher (`tests/integration/ros2/publisher.py`) writes its own `ground_truth.json` from the constants it publishes with: the topic list with types, publisher and subscriber counts and QoS, a synthetic 541-beam scan whose ranges are a closed formula (`1.0 + j * 0.001` m) with its sector minima, the configured rates, `use_sim_time` per node, the RMW and the distro. In every cell of the CI matrix (Humble and Jazzy, Fast DDS and Cyclone) `drive.py` calls TopicForge over MCP stdio against that graph and `compare.py` checks the answers against the truth and against a bag recorded in the same run (read independently with `rosbags`); a mismatch fails the job and the report is uploaded as an artifact. What it does not do: it has no physical simulator, so it says nothing about the real-time-factor effects the OmniSim runs show (wall rate against simulated rate), and the rate verdicts are warnings, not failures, because they depend on the machine (a frequency more than 50 percent off the configured one fails, and only when at least 10 messages span 0.9 s or more). The OmniSim kit stays the external confirmation.
