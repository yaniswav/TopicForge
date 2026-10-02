"""OpenDDS adapter stub.

Imported only when `services.factory` resolves the DDS backend to
`opendds`. `pyopendds` is not maintained on PyPI, so the stub makes an
explicit `TOPICFORGE_DDS_BACKEND=opendds` fail with a clear error instead
of falling back to mock silently.
"""

from topicforge.adapters.dds_opendds.adapter import OpenDdsAdapter

__all__ = ["OpenDdsAdapter"]
