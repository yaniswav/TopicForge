# TopicForge (ROS 2 / DDS)

TopicForge is a read-only MCP server for ROS 2 and DDS. It observes a robot stack;
it cannot publish, command or change anything. There is no write path in the code.

## Tools (twelve, all read-only)

- `health_check`: environment and mode introspection. Call it first; it always succeeds.
- `list_topics`, `get_topic_info`, `sample_messages`: discover and peek the ROS 2 graph.
- `analyze_bag`, `peek_bag_samples`: summarize and read recorded `.mcap`, `.db3`, `.bag` files.
- `list_participants`, `participant_events`: who is on the DDS domain, and when they appeared or vanished.
- `list_endpoints`: DDS writers and readers with their QoS, plus per-topic orphans.
- `detect_qos_mismatches`: incompatible QoS between readers and writers.
- `peek_dds_samples`, `topic_metrics`: raw DDS samples, observed frequency, sequence gaps, latency.

## Working rules

- Every response except `health_check` carries `mode_effective` (`live` or `mock`).
  If it says `mock`, the data is fixtures, not the real robot: say so to the user.
- The server observes one DDS domain, fixed at startup (`TOPICFORGE_DDS_DOMAIN_ID`).
  Changing the domain needs a restart.
- Report what the tools return. If a tool errors or returns nothing, say that
  rather than guessing at the state of the robot.
