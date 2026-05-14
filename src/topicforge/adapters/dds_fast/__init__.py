"""eProsima Fast DDS adapter — lazy-imported when DDS module is active.

This package is **never imported** unless `services.factory` resolves
the DDS backend to `fast` (or `auto` with fastdds installable).
Mock-only installs and Cyclone-only installs do not pay the `fastdds`
import cost.
"""

from topicforge.adapters.dds_fast.adapter import FastDdsAdapter

__all__ = ["FastDdsAdapter"]
