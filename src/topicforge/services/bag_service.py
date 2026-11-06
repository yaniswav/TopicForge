"""Bag service: the `BagService` facade over `rosbags`.

The reading itself (spans, start times, decoding, sample peeks) lives in
`adapters/common/bag_read.py` so the adapters can use it without importing
this layer.
"""

from __future__ import annotations

import logging

from topicforge.adapters.common.bag_read import (
    is_rosbags_available,
    peek_bag_samples,
)
from topicforge.adapters.common.log_filter import LogFilter
from topicforge.models import SampleResult

log = logging.getLogger(__name__)


class BagService:
    """Facade over `rosbags`.

    Bag analysis is not exposed here: nothing in `src` called it. The reader
    stays available as `adapters/common/bag_read.analyze_bag_file`.

    * `peek_samples(path, topic, count)` returns decoded samples as a
      `SampleResult`, with `_decode_status` annotations.
    """

    def __init__(self) -> None:
        if not is_rosbags_available():
            # Construction does not raise; the methods do.
            log.debug("BagService instantiated without `rosbags` ; methods will raise.")

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
