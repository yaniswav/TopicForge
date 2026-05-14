"""Adapter protocol.

Adapters are the *only* place in the codebase that may know how to talk to a
specific backend (mock fixtures, the `ros2` CLI, or a DDS middleware binding).
Services depend on this protocol; tools depend on services. That separation
is what makes the codebase testable without ROS2 or any DDS SDK installed.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from topicforge.models import (
    BagAnalysis,
    MessageSample,
    MismatchReport,
    ParticipantInfo,
    SampleResult,
    TopicInfo,
)

AdapterName = Literal["mock", "ros2_cli", "cyclone", "rti"]
"""Implementation tag for the active adapter.

Internal — used by factory wiring and logging. Distinct from
`EffectiveMode`, which is the MCP wire contract surfaced to clients.
"""

EffectiveMode = Literal["mock", "live"]
"""Runtime mode surfaced to MCP clients via the `mode_effective` field.

Stable across implementation changes — a new live adapter (`cyclone`,
`rti`, future `rclpy`) reports `effective_mode == "live"` while carrying
a distinct `name`. Adding a new value here would be a wire-breaking
change for MCP clients ; do not.
"""


class AdapterError(RuntimeError):
    """Raised when an adapter cannot fulfill a request.

    Carries a clear, user-facing message; tool handlers translate this into
    a structured error envelope returned to the MCP client.
    """


@runtime_checkable
class MiddlewareAdapter(Protocol):
    """Uniform read-only interface over a ROS2 or DDS middleware backend.

    Generalizes the earlier `RosAdapter` protocol to cover both ROS2
    graph introspection and bare DDS observability under a single
    contract. The ROS2 methods (`list_topics`, `get_topic_info`,
    `sample_messages`, `analyze_bag`) and the DDS methods
    (`list_participants`, `detect_qos_mismatches`, `peek_dds_samples`)
    are both required by the protocol — but backends are free to raise
    `AdapterError` on the half they do not natively serve. The
    `Ros2CliAdapter`, for example, raises on the DDS methods ; a
    `CycloneDdsAdapter` raises on `analyze_bag`.

    Implementations must be safe to construct lazily — `is_available()`
    is the contract for "can this adapter actually serve requests right
    now?".
    """

    name: AdapterName

    @property
    def effective_mode(self) -> EffectiveMode:
        """Runtime mode this adapter serves responses in (`live` or `mock`).

        Distinct from `name`: `name` identifies the adapter implementation
        (`mock`, `ros2_cli`, `cyclone`, `rti`, ...). `effective_mode`
        collapses to the wire contract exposed to MCP clients via
        `mode_effective` on every tool response, so different live
        backends all report `effective_mode == "live"`.
        """

    def is_available(self) -> bool: ...

    # ROS2 graph methods. Required by the protocol ; DDS-only backends
    # raise AdapterError on these.
    def list_topics(self) -> list[TopicInfo]: ...

    def get_topic_info(self, topic: str) -> TopicInfo: ...

    def sample_messages(self, topic: str, count: int) -> list[MessageSample]: ...

    def analyze_bag(self, path: str) -> BagAnalysis: ...

    # DDS module methods. Required by the protocol ; the ROS2 CLI
    # backend raises AdapterError on these to signal that the DDS
    # module is not active in the current configuration.
    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]: ...

    def detect_qos_mismatches(self, topic: str | None = None) -> list[MismatchReport]: ...

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult: ...


# Backward-compat alias. External code importing `RosAdapter` continues
# to type-check against the broader `MiddlewareAdapter` shape. Will be
# preserved through the v0.2.x line ; future deprecation is documented
# in CHANGELOG when it happens.
RosAdapter = MiddlewareAdapter
