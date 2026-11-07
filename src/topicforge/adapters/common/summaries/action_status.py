"""Summary of `action_msgs/msg/GoalStatusArray`: the number of goals per status."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from topicforge.adapters.common.summaries._common import as_int, sentence
from topicforge.models.summaries import GoalStatusSummary

# `action_msgs/msg/GoalStatus` constants: 0 unknown, 1 accepted ... 6 aborted.
_STATUS_NAMES = {
    1: "accepted",
    2: "executing",
    3: "canceling",
    4: "succeeded",
    5: "canceled",
    6: "aborted",
}


def summarize_goal_status(
    payload: Mapping[str, Any], cut_fields: frozenset[str] = frozenset()
) -> GoalStatusSummary | None:
    """Count the goals of a decoded `GoalStatusArray` by status.

    `None` when `status_list` is not the whole list (replaced by the CLI's `<sequence ...>` text
    or listed in `cut_fields`). A status outside 1..6, or one that is not an integer, counts as
    `unknown` and is said in `note`.
    """
    goals = payload.get("status_list")
    if not isinstance(goals, list) or "status_list" in cut_fields:
        return None
    counts = dict.fromkeys(_STATUS_NAMES.values(), 0)
    unknown = 0
    odd = 0
    for goal in goals:
        status = as_int(goal.get("status")) if isinstance(goal, Mapping) else None
        name = _STATUS_NAMES.get(status) if status is not None else None
        if name is not None:
            counts[name] += 1
        else:
            unknown += 1
            odd += status != 0
    problems = [f"{odd} goal(s) have a status that is not 0..6"] if odd else []
    return GoalStatusSummary(
        summary_type="goal_status",
        goal_count=len(goals),
        unknown=unknown,
        note=sentence(problems),
        **counts,
    )
