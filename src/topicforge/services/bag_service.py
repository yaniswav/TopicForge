"""Bag service: the `BagService` facade over `rosbags`.

The reading itself (spans, start times, decoding, sample peeks) lives in
`adapters/common/bag_read.py` so the adapters can use it without importing
this layer.
"""

from __future__ import annotations

import logging

from topicforge.adapters.common.bag_read import (
    analyze_bag_file,
    is_rosbags_available,
    peek_bag_samples,
)
from topicforge.adapters.common.log_filter import LogFilter
from topicforge.models import BagAnalysis, SampleResult

log = logging.getLogger(__name__)


class BagService:
    """Facade over `rosbags`.

    * `analyze(path)` returns a `BagAnalysis` with per-topic stats, format
      and duration. It raises when `rosbags` is missing, so callers check
      `is_rosbags_available()` and fall back to the CLI parser.
    * `peek_samples(path, topic, count)` returns decoded samples as a
      `SampleResult`, with `_decode_status` annotations.
    """

    def __init__(self) -> None:
        if not is_rosbags_available():
            # Construction does not raise; the methods do.
            log.debug("BagService instantiated without `rosbags` ; methods will raise.")

    def analyze(self, path: str, *, mode_effective: str = "live") -> BagAnalysis:
        """Read `path` with `rosbags` into a `BagAnalysis`; see `analyze_bag_file`."""
        return analyze_bag_file(path, mode_effective=mode_effective)

    def peek_samples(
        self,
        path: str,
        topic: str,
        count: int,
        *,
        mode_effective: str = "live",
        start_s: float | None = None,
        end_s: float | None = None,
        log_filter: LogFilter | None = None,
    ) -> SampleResult:
        """Up to `count` decoded samples for `topic` from `path`; see `peek_bag_samples`."""
        return peek_bag_samples(
            path,
            topic,
            count,
            mode_effective=mode_effective,
            start_s=start_s,
            end_s=end_s,
            log_filter=log_filter,
        )
