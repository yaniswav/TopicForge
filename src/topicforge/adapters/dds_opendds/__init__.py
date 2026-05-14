"""OpenDDS adapter — v0.4.0 Phase 1.5 stub.

This package is **never imported** unless `services.factory` resolves
the DDS backend to `opendds` (or `auto` with `pyopendds` installable).

v0.4.0 Phase 1.5 ships a stub adapter because `pyopendds` is not yet
maintained on PyPI as of 2026-05-14. The factory routes here so users
running `TOPICFORGE_DDS_BACKEND=opendds` explicitly see a clear error
rather than a silent mock fallback. The real adapter will replace this
stub when a Python binding stabilizes upstream.
"""

from topicforge.adapters.dds_opendds.adapter import OpenDdsAdapter

__all__ = ["OpenDdsAdapter"]
