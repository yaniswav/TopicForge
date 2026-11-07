"""`graph_diff` and the robot model: a changed `/robot_description` shows in the diff.

Scripted adapters, no ROS 2. The hash is read only from what the adapter already serves: one
latched message for a live graph (same bound as `inspect_tf`), the first message of a bag.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.test_graph_diff import Clock, FakeAdapter, bag, bag_topic, no_recent_cli  # noqa: F401
from tests.test_tf_tools import _string_result
from topicforge import budget
from topicforge.adapters.base import AdapterError
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.services import Inspector
from topicforge.services.graph_diff import GraphDiffService
from topicforge.services.graph_snapshot import build_live_snapshot

STRING = "std_msgs/msg/String"
DESC = "/robot_description"


class ModelAdapter(FakeAdapter):
    """A graph whose `/robot_description` text, and bag copies of it, are scripted."""

    def __init__(self, text: str | None = "<robot name='a'><link name='base'/></robot>") -> None:
        super().__init__()
        self.text = text
        self.reads: list[tuple[str, float]] = []
        self.bag_texts: dict[str, str | Exception] = {}
        self.topics[DESC] = (STRING, 1, 0)

    def sample_messages(self, topic: str, count: int, **kw: Any) -> Any:
        self.reads.append((topic, kw["timeout_s"]))
        if isinstance(self.text, Exception):
            raise self.text
        return _string_result(self.text)

    def peek_bag_samples(self, path: str, topic: str, count: int, **kw: Any) -> Any:
        answer = self.bag_texts.get(path)
        if isinstance(answer, Exception):
            raise answer
        return _string_result(answer)


def _service(adapter: ModelAdapter) -> GraphDiffService:
    return GraphDiffService(adapter, Clock())  # type: ignore[arg-type]


def test_a_changed_robot_description_is_a_change() -> None:
    adapter = ModelAdapter()
    service = _service(adapter)
    service.diff("previous", None)
    adapter.text = "<robot name='a'><link name='base'/><link name='arm'/></robot>"
    diff = service.diff("previous", None)
    (change,) = diff.changes
    assert (change.kind, change.change, change.name, change.field) == (
        "topic",
        "changed",
        DESC,
        "sha256",
    )
    assert change.before != change.after and len(change.after or "") == 12
    assert "robot model" in change.text


def test_the_same_robot_description_is_no_change_and_the_read_is_bounded() -> None:
    adapter = ModelAdapter()
    service = _service(adapter)
    service.diff("previous", None)
    diff = service.diff("previous", None)
    assert diff.changes == [] and "robot_description" not in diff.not_compared
    assert adapter.reads == [(DESC, 6.0), (DESC, 6.0)]


def test_whitespace_around_the_text_is_not_a_change() -> None:
    adapter = ModelAdapter("<robot/>")
    service = _service(adapter)
    service.diff("previous", None)
    adapter.text = "<robot/>\n"
    assert service.diff("previous", None).changes == []


def test_no_publisher_means_no_read() -> None:
    adapter = ModelAdapter()
    adapter.topics[DESC] = (STRING, 0, 1)
    assert build_live_snapshot(adapter, 0).description_hash is None  # type: ignore[arg-type]
    assert adapter.reads == []


def test_a_failed_read_is_unknown_not_a_change() -> None:
    adapter = ModelAdapter()
    service = _service(adapter)
    service.diff("previous", None)
    adapter.text = AdapterError("echo failed")  # type: ignore[assignment]
    diff = service.diff("previous", None)
    assert diff.changes == []
    assert "one side only" in diff.not_compared["robot_description"]


def test_no_budget_skips_the_read(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = ModelAdapter()
    monkeypatch.setattr(budget, "remaining", lambda: 4.0)  # 4 s left, 3 s kept back: 1 s < 2 s
    assert build_live_snapshot(adapter, 0).description_hash is None  # type: ignore[arg-type]
    assert adapter.reads == []


def test_a_bag_baseline_with_the_model_is_compared() -> None:
    adapter = ModelAdapter("<robot name='new'/>")
    adapter.bags["/b/x"] = bag(bag_topic(DESC, STRING), bag_topic("/scan", "t"))
    adapter.bag_texts["/b/x"] = "<robot name='old'/>"
    diff = _service(adapter).diff("/b/x", None)
    assert [(c.kind, c.name, c.field) for c in diff.changes if c.name == DESC] == [
        ("topic", DESC, "sha256")
    ]


def test_a_bag_without_the_model_reports_unknown_not_a_change() -> None:
    adapter = ModelAdapter()
    adapter.bags["/b/x"] = bag(bag_topic("/scan", "t"))
    diff = _service(adapter).diff("/b/x", None)
    assert all(c.field != "sha256" for c in diff.changes)
    assert "one side only" in diff.not_compared["robot_description"]


def test_a_bag_that_cannot_be_peeked_is_unknown() -> None:
    adapter = ModelAdapter()
    adapter.bags["/b/x"] = bag(bag_topic(DESC, STRING))
    adapter.bag_texts["/b/x"] = AdapterError("unreadable")
    diff = _service(adapter).diff("/b/x", None)
    assert all(c.field != "sha256" for c in diff.changes)


def test_two_bags_with_the_same_model_have_no_change() -> None:
    adapter = ModelAdapter()
    for path in ("/b/x", "/b/y"):
        adapter.bags[path] = bag(bag_topic(DESC, STRING), path=path)
        adapter.bag_texts[path] = "<robot/>"
    assert _service(adapter).diff("/b/x", "/b/y").changes == []


def test_the_mock_graph_adds_no_robot_description_difference() -> None:
    inspector = Inspector(MockAdapter())
    first = inspector.graph_diff()
    assert DESC not in {c.name for c in first.changes}
    assert "robot_description" not in first.not_compared
    assert inspector.graph_diff().changes == []
