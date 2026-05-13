"""Adapters — the only layer that knows how to talk to a specific backend."""

from topicforge.adapters.base import AdapterError, RosAdapter

__all__ = ["AdapterError", "RosAdapter"]
