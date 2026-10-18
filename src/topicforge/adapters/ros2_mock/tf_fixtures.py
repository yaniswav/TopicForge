"""Deterministic TF capture of the mock robot, for `inspect_tf`.

The tree: `map -> odom -> base_link`, with `laser`, `camera_link` and `imu_link` under
`base_link` and `camera_optical_frame` under `camera_link`. The fixed frames come from
`/tf_static`. `odom -> base_link` moves at 50 Hz for the whole 5 s capture; `map ->
odom` (a localizer) published at 1 Hz and went quiet 4 s before the end: the one
finding of the mock, a `stale` frame.
"""

from __future__ import annotations

from topicforge.adapters.ros2_mock import fixtures
from topicforge.models import TfCapture, TfSample

# The mock capture is fixed: `window_s` does not change it.
MOCK_TF_WINDOW_S = 5.0
_PERIOD_NS = 20_000_000  # 50 Hz
_LATENCY_NS = 3_000_000
_MAP_ODOM_PERIOD_NS = 1_000_000_000
_MAP_ODOM_LAST_NS = 1_000_000_000  # last published 4 s before the newest transform
_NS = 1_000_000_000

MOCK_TF_NOTE = "Mock mode: a fixed 5 s capture of the demo robot; `window_s` does not change it."


def mock_tf_capture() -> TfCapture:
    """The fixed capture: four static transforms, 251 `odom` and 2 `map` samples on `/tf`."""
    base = fixtures.MOCK_BASE_TS_NS
    static = [
        TfSample(parent=a, child=b, stamp_ns=base, received_ns=base)
        for a, b in fixtures.MOCK_STATIC_EDGES
    ]
    steps = int(MOCK_TF_WINDOW_S * _NS) // _PERIOD_NS
    dynamic = [
        TfSample(
            parent="odom",
            child="base_link",
            stamp_ns=base + i * _PERIOD_NS,
            received_ns=base + i * _PERIOD_NS + _LATENCY_NS,
        )
        for i in range(steps + 1)
    ]
    dynamic += [
        TfSample(
            parent="map",
            child="odom",
            stamp_ns=base + t,
            received_ns=base + t + _LATENCY_NS,
        )
        for t in range(0, _MAP_ODOM_LAST_NS + 1, _MAP_ODOM_PERIOD_NS)
    ]
    dynamic.sort(key=lambda s: s.received_ns)
    return TfCapture(
        static=static,
        dynamic=dynamic,
        window_actual_s=MOCK_TF_WINDOW_S,
        notes=[MOCK_TF_NOTE],
    )
