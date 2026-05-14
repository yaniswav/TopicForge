"""Dust DDS adapter — v0.4.0 Phase 1.5 stub.

This package is **never imported** unless `services.factory` resolves
the DDS backend to `dust`. Even thinner than the OpenDDS stub :
Dust DDS is a Rust-native implementation and no Python binding is
maintained on PyPI as of 2026-05-14. `is_available()` always returns
False ; the factory falls back transparently.
"""

from topicforge.adapters.dds_dust.adapter import DustDdsAdapter

__all__ = ["DustDdsAdapter"]
