"""Cross-backend helpers — pure logic shared between adapters."""

from topicforge.adapters.common.dds_helpers import (
    DDS_ONLY_ERROR_MSG,
    VendorTag,
    canonicalize_vendor_id,
    format_guid,
)
from topicforge.adapters.common.lifecycle import MAX_EVENTS, LifecycleBuffer
from topicforge.adapters.common.metrics_buffer import (
    MAX_SAMPLES_PER_TOPIC,
    MetricsBuffer,
    MetricsSample,
)
from topicforge.adapters.common.qos_analyzer import detect_mismatches
from topicforge.adapters.common.xtypes import (
    DecodeStatus,
    annotate_full,
    annotate_partial,
    annotate_raw,
)

__all__ = [
    "DDS_ONLY_ERROR_MSG",
    "MAX_EVENTS",
    "MAX_SAMPLES_PER_TOPIC",
    "DecodeStatus",
    "LifecycleBuffer",
    "MetricsBuffer",
    "MetricsSample",
    "VendorTag",
    "annotate_full",
    "annotate_partial",
    "annotate_raw",
    "canonicalize_vendor_id",
    "detect_mismatches",
    "format_guid",
]
