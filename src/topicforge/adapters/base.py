"""Adapter protocol.

Adapters are the *only* place in the codebase that may know how to talk to a
specific backend (mock fixtures, the `ros2` CLI, or a future `rclpy` adapter).
Services depend on this protocol; tools depend on services. That separation
is what makes the codebase testable without ROS2 installed.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from topicforge.models import BagAnalysis, MessageSample, TopicInfo

AdapterName = Literal["mock", "live"]


class AdapterError(RuntimeError):
    """Raised when an adapter cannot fulfill a request.

    Carries a clear, user-facing message; tool handlers translate this into
    a structured error envelope returned to the MCP client.
    """


@runtime_checkable
class RosAdapter(Protocol):
    """Uniform read-only interface over a ROS2 environment.

    Implementations must be safe to construct lazily — `is_available()` is
    the contract for "can this adapter actually serve requests right now?".
    """

    name: AdapterName

    def is_available(self) -> bool: ...

    def list_topics(self) -> list[TopicInfo]: ...

    def get_topic_info(self, topic: str) -> TopicInfo: ...

    def sample_messages(self, topic: str, count: int) -> list[MessageSample]: ...

    def analyze_bag(self, path: str) -> BagAnalysis: ...
