"""Dust DDS adapter stub.

Imported only when `services.factory` resolves the DDS backend to `dust`.
Dust DDS is Rust-native with no maintained Python binding, so
`is_available()` is always False and the factory falls back.
"""

from topicforge.adapters.dds_dust.adapter import DustDdsAdapter

__all__ = ["DustDdsAdapter"]
