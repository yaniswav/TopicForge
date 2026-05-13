"""Health service — environment & mode introspection."""

from __future__ import annotations

import os
import shutil

from topicforge import __version__
from topicforge.config import Settings
from topicforge.models import HealthReport
from topicforge.services.inspector import MAX_SAMPLE_COUNT


class HealthService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def report(self) -> HealthReport:
        """Build a HealthReport for the current environment.

        Never raises. `health_check` is the tool a user will reach for when
        things look broken, so it must always answer.
        """
        ros2_path = shutil.which(self._settings.ros2_executable)
        return HealthReport(
            mode=self._settings.effective_mode,
            requested_mode=self._settings.mode,
            ros2_available=ros2_path is not None,
            ros2_distro=os.environ.get("ROS_DISTRO"),
            server_version=__version__,
            max_sample_count=MAX_SAMPLE_COUNT,
        )
