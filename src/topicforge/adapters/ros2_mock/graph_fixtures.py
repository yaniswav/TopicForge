"""Graph-diff fixtures of the mock robot.

`graph_diff` needs, in mock mode, a baseline to compare the demo robot with. It is derived
from the live fixtures (`fixtures.MOCK_TOPICS`, `node_fixtures`) by removing three things,
so it never drifts from them: the node `/imu_driver`, the topic `/imu/data`, and the
`/scan` writer QoS, which the baseline had as reliable. The first mock call therefore
reports exactly three changes (a node, a topic and a QoS), and a second call reports none.
"""

from __future__ import annotations

from topicforge.adapters.ros2_mock.fixtures import MOCK_TOPICS

MOCK_BASELINE_MISSING_NODE = "/imu_driver"
MOCK_BASELINE_MISSING_TOPIC = "/imu/data"
MOCK_BASELINE_SCAN_TOPIC = "/scan"
MOCK_BASELINE_SCAN_QOS = "reliable/volatile"


def mock_writer_qos() -> dict[str, str]:
    """`{topic: "reliability/durability"}` of the mock topics' publishers, lowercase."""
    return {
        t.name: f"{t.publisher_qos.reliability}/{t.publisher_qos.durability}"
        for t in MOCK_TOPICS
        if t.publisher_qos is not None
    }
