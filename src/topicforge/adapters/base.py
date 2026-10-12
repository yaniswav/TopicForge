"""Adapter protocol.

Adapters are the only code that talks to a specific backend (mock fixtures,
the `ros2` CLI, a DDS binding). Services depend on this protocol, so the
codebase is testable without ROS2 or any DDS SDK installed.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from topicforge.constants import DEFAULT_MAX_ARRAY_LENGTH, DEFAULT_SAMPLE_TIMEOUT_S
from topicforge.models import (
    BagAnalysis,
    EndpointListing,
    MismatchScan,
    NodeInfo,
    NodeListing,
    ParticipantEvent,
    ParticipantInfo,
    SampleResult,
    TopicInfo,
    TopicListItem,
    TopicMetrics,
)

AdapterName = Literal[
    "mock",
    "ros2_cli",
    "cyclone",
    "fast",
    "ros2_cli+cyclone",
    "ros2_cli+fast",
]
"""Implementation tag for the active adapter.

Used by factory wiring and logging; `EffectiveMode` is the value clients
see. The hyphenated forms are emitted by `CompositeAdapter` when the ROS2
CLI and a DDS backend serve the bus together. `HealthService` derives
`ros_backend` and `dds_backend` from this tag, so new values must stay
parseable there.
"""

EffectiveMode = Literal["mock", "live"]
"""Runtime mode surfaced to MCP clients via the `mode_effective` field.

Every live adapter (`cyclone`, a future `rclpy`) reports `"live"` while
keeping its own `name`. Adding a value here breaks the wire contract.
"""


class AdapterError(RuntimeError):
    """Raised when an adapter cannot fulfill a request.

    Carries a user-safe message. `guarded` re-raises it as the SDK `ToolError`, which becomes
    an `isError: true` tool result with this message.
    """


@runtime_checkable
class MiddlewareAdapter(Protocol):
    """Uniform read-only interface over a ROS2 or DDS middleware backend.

    Covers ROS2 graph introspection and DDS observability under one
    contract. Every method is required, but a backend raises `AdapterError`
    on the half it does not serve: `Ros2CliAdapter` on the DDS methods,
    `CycloneDdsAdapter` on `analyze_bag`.

    Construction must be cheap and side-effect free; `is_available()`
    reports whether the adapter can serve requests now.
    """

    name: AdapterName

    @property
    def effective_mode(self) -> EffectiveMode:
        """Runtime mode this adapter serves responses in (`live` or `mock`).

        `name` identifies the implementation; this is the `mode_effective`
        value on every tool response, so all live backends report `"live"`.
        """

    def is_available(self) -> bool: ...

    # ROS2 graph methods. DDS-only backends raise AdapterError.
    def list_topics(self) -> list[TopicListItem]: ...

    def get_topic_info(self, topic: str) -> TopicInfo: ...

    def sample_messages(
        self,
        topic: str,
        count: int,
        *,
        max_array_length: int | None = DEFAULT_MAX_ARRAY_LENGTH,
        arrays_summary_only: bool = False,
        timeout_s: float = DEFAULT_SAMPLE_TIMEOUT_S,
    ) -> SampleResult: ...

    def analyze_bag(self, path: str) -> BagAnalysis: ...

    def list_nodes(self) -> NodeListing: ...

    def get_node_info(self, node: str, timeout_s: float = 8.0) -> NodeInfo: ...

    # DDS methods. The ROS2 CLI backend raises AdapterError when no DDS
    # backend is configured.
    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]: ...

    def detect_qos_mismatches(self, topic: str | None = None) -> MismatchScan: ...

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult: ...

    def participant_events(
        self, domain_id: int = 0, lookback_s: int = 300
    ) -> list[ParticipantEvent]: ...

    def topic_metrics(self, topic: str, window_s: int = 60, domain_id: int = 0) -> TopicMetrics: ...

    def peek_bag_samples(self, path: str, topic: str, count: int) -> SampleResult: ...

    def list_endpoints(
        self,
        topic: str | None = None,
        participant_guid: str | None = None,
        include_observer: bool = False,
        include_departed: bool = False,
        include_internal: bool = False,
    ) -> EndpointListing: ...
