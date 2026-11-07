"""Stand-in for `rclpy.qos`: the profile `sensor_data` and plain policy names."""

from types import SimpleNamespace

QoSDurabilityPolicy = SimpleNamespace(TRANSIENT_LOCAL="transient_local", VOLATILE="volatile")
QoSHistoryPolicy = SimpleNamespace(KEEP_LAST="keep_last")
QoSReliabilityPolicy = SimpleNamespace(RELIABLE="reliable", BEST_EFFORT="best_effort")
qos_profile_sensor_data = SimpleNamespace(
    reliability="best_effort", durability="volatile", history="keep_last", depth=5
)
