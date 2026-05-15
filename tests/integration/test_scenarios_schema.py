"""Pure-Python schema validation of the scenario JSON files.

Runs in the default `make check` pipeline — no SDK, no Docker, no
running bus. Pins the structural contract every scenario file must
follow ; future scenario authors get a fast feedback loop.
"""

from __future__ import annotations

from pathlib import Path

# Tools we ship as of v0.4.0 Phase 2 — every scenario assertion must
# target one of these. Mirrors `tests/test_tools_integration.py::MVP_TOOLS`.
_KNOWN_TOOLS: set[str] = {
    "health_check",
    "list_topics",
    "get_topic_info",
    "sample_messages",
    "analyze_bag",
    "list_participants",
    "detect_qos_mismatches",
    "peek_dds_samples",
    "participant_events",
    "topic_metrics",
}

# Vendor tags accepted in scenario `required_vendors` lists.
_KNOWN_VENDORS: set[str] = {
    "cyclone",
    "fast",
    "opendds",
    "dust",
    "rti",
    "opensplice",
    "coredx",
    "intercom",
}


def test_scenarios_directory_exists(scenarios_dir: Path) -> None:
    assert scenarios_dir.is_dir()


def test_scenarios_directory_is_non_empty(scenarios_dir: Path) -> None:
    files = list(scenarios_dir.glob("*.json"))
    assert len(files) >= 4, (
        f"Expected at least 4 scenario JSON files, found {len(files)}. "
        "Phase 2.2 ships 6 scenarios — see docs/projet-file/mcp-02-spec.md "
        "for the canonical scope."
    )


def test_every_scenario_has_required_top_level_keys(
    all_scenarios: list[dict[str, object]],
) -> None:
    required = {"name", "description", "required_vendors", "setup", "assertions"}
    for scenario in all_scenarios:
        missing = required - set(scenario.keys())
        assert not missing, (
            f"Scenario {scenario.get('name', '<unnamed>')!r} missing required keys: {missing}"
        )


def test_every_assertion_targets_a_known_tool(
    all_scenarios: list[dict[str, object]],
) -> None:
    for scenario in all_scenarios:
        assertions = scenario["assertions"]
        assert isinstance(assertions, list)
        for assertion in assertions:
            assert isinstance(assertion, dict)
            tool = assertion.get("tool")
            assert tool in _KNOWN_TOOLS, (
                f"Scenario {scenario['name']!r} references unknown tool "
                f"{tool!r}. Allowed: {sorted(_KNOWN_TOOLS)}"
            )


def test_every_required_vendor_is_known(
    all_scenarios: list[dict[str, object]],
) -> None:
    for scenario in all_scenarios:
        vendors = scenario["required_vendors"]
        assert isinstance(vendors, list)
        for vendor in vendors:
            assert vendor in _KNOWN_VENDORS, (
                f"Scenario {scenario['name']!r} requires unknown vendor "
                f"{vendor!r}. Allowed: {sorted(_KNOWN_VENDORS)}"
            )


def test_every_publisher_has_topic_and_vendor(
    all_scenarios: list[dict[str, object]],
) -> None:
    for scenario in all_scenarios:
        setup = scenario["setup"]
        assert isinstance(setup, dict)
        publishers = setup.get("publishers", [])
        assert isinstance(publishers, list)
        for pub in publishers:
            assert isinstance(pub, dict)
            assert "vendor" in pub, f"publisher in {scenario['name']!r} missing 'vendor'"
            assert "topic" in pub, f"publisher in {scenario['name']!r} missing 'topic'"


def test_topic_metrics_window_within_bounds(
    all_scenarios: list[dict[str, object]],
) -> None:
    """Scenarios targeting topic_metrics must respect the 1..3600 window."""
    for scenario in all_scenarios:
        for assertion in scenario["assertions"]:
            if assertion.get("tool") != "topic_metrics":
                continue
            window = assertion.get("args", {}).get("window_seconds")
            if window is not None:
                assert 1 <= int(window) <= 3600, (
                    f"Scenario {scenario['name']!r} window_seconds={window} out of range 1..3600."
                )


def test_participant_events_lookback_within_bounds(
    all_scenarios: list[dict[str, object]],
) -> None:
    """Scenarios targeting participant_events must respect the 1..86400 lookback."""
    for scenario in all_scenarios:
        for assertion in scenario["assertions"]:
            if assertion.get("tool") != "participant_events":
                continue
            lookback = assertion.get("args", {}).get("lookback_seconds")
            if lookback is not None:
                assert 1 <= int(lookback) <= 86400, (
                    f"Scenario {scenario['name']!r} lookback_seconds={lookback} "
                    "out of range 1..86400."
                )


def test_unique_scenario_names(all_scenarios: list[dict[str, object]]) -> None:
    names = [s["name"] for s in all_scenarios]
    assert len(names) == len(set(names)), f"Duplicate scenario names detected: {names}"
