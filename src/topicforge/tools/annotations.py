"""MCP `ToolAnnotations` shared by every TopicForge tool.

TopicForge has no write path. The protocol-level declaration of that fact is
built here, in one place, so a tool cannot be registered with a weaker
read-only base by accident. `tests/test_tool_annotations.py` fails closed when
a tool lacks these annotations.
"""

from __future__ import annotations

from mcp.types import ToolAnnotations


def read_only_annotations(title: str, *, open_world: bool) -> ToolAnnotations:
    """Build the annotations for one read-only tool.

    Args:
        title: Short human-readable tool name.
        open_world: True when the tool observes a live ROS 2 graph or DDS bus
            (state outside this process); False when it only reads the local
            environment or local files.
    """
    return ToolAnnotations(
        title=title,
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=open_world,
    )


__all__ = ["read_only_annotations"]
