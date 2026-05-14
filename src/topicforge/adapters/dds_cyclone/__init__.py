"""Cyclone DDS adapter — lazy-imported when DDS module is active.

This package is **never imported** unless `services.factory` resolves
the DDS backend to `cyclone` (or `auto` with cyclonedds installable).
Mock-only installs and ROS2-only installs do not pay the cyclonedds
import cost.
"""

from topicforge.adapters.dds_cyclone.adapter import CycloneDdsAdapter

__all__ = ["CycloneDdsAdapter"]
