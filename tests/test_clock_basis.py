"""Clock basis, stamp age and stamp regressions (docs/CONTRACT.md section 9.10)."""

from __future__ import annotations

import pytest

from topicforge.services.summaries import compute_rate
from topicforge.services.summaries.clock import classify_stamp, compute_clock

S = 1_000_000_000
T0 = 1_760_000_000 * S
H = "header"


def _wall(n: int, age_ns: int = 20_000_000, step: int = S // 50) -> list[tuple[int, int, str]]:
    return [(T0 + i * step, T0 + i * step - age_ns, H) for i in range(n)]


def test_wall_stamps_give_wall_basis_and_age() -> None:
    stats = compute_clock(_wall(30))
    assert stats.basis == "wall"
    assert stats.age_median_s == pytest.approx(0.02)
    assert stats.age_p95_s == pytest.approx(0.02)


def test_sim_stamps_give_sim_basis_and_no_age() -> None:
    stats = compute_clock([(T0 + i * S, 100 * S + i * S, H) for i in range(10)])
    assert stats.basis == "sim"
    assert stats.age_median_s is None
    assert stats.age_p95_s is None


def test_zero_stamps_and_unstamped_sources_give_none() -> None:
    assert compute_clock([(T0 + i, 0, "none") for i in range(5)]).basis == "none"
    assert compute_clock([(T0 + i, T0, "recorded") for i in range(5)]).basis == "none"
    assert compute_clock([]).basis == "none"


def test_sim_and_wall_publishers_give_mixed_without_age() -> None:
    items = [(T0 + i * S, (T0 if i % 2 else 50 * S) + i * S, H) for i in range(10)]
    stats = compute_clock(items)
    assert stats.basis == "mixed"
    assert stats.age_median_s is None


def test_zero_next_to_stamped_messages_is_mixed() -> None:
    items = [(T0, T0, H), (T0 + S, 0, "none")]
    assert compute_clock(items).basis == "mixed"


def test_negative_age_when_publisher_clock_is_ahead() -> None:
    stats = compute_clock(_wall(10, age_ns=-500_000_000))
    assert stats.age_median_s == pytest.approx(-0.5)


def test_p95_is_nearest_rank_and_max_under_twenty() -> None:
    few = [(T0 + i * S, T0 + i * S - i * 1_000_000, H) for i in range(10)]
    assert compute_clock(few).age_p95_s == pytest.approx(0.009)
    many = [(T0 + i * S, T0 + i * S - i * 1_000_000, H) for i in range(100)]
    assert compute_clock(many).age_p95_s == pytest.approx(0.094)
    assert compute_clock(many).age_median_s == pytest.approx(0.0495)


def test_regressions_count_strict_decreases_in_basis_order() -> None:
    items = [(T0 + i * S, 10 * S + i * S - (2 * S if i in (3, 6) else 0), H) for i in range(8)]
    assert compute_clock(items).regression_count == 2


def test_equal_stamps_are_not_regressions() -> None:
    items = [(T0 + i * S, 5 * S, H) for i in range(6)]
    assert compute_clock(items).regression_count == 0


def test_unordered_input_is_sorted_by_basis_first() -> None:
    items = [(T0 + 2 * S, 3 * S, H), (T0, 1 * S, H), (T0 + S, 2 * S, H)]
    assert compute_clock(items).regression_count == 0


def test_zero_stamps_are_skipped_by_regressions() -> None:
    items = [(T0, 5 * S, H), (T0 + S, 0, "none"), (T0 + 2 * S, 6 * S, H)]
    assert compute_clock(items).regression_count == 0


def test_classify_stamp_threshold() -> None:
    assert classify_stamp(10**18, H) == "wall"
    assert classify_stamp(10**18 - 1, H) == "sim"
    assert classify_stamp(0, H) == "zero"
    assert classify_stamp(10**18, "recorded") == "zero"


def test_compute_rate_carries_the_clock_fields() -> None:
    stamped = _wall(10)
    rate = compute_rate([b for b, _, _ in stamped], basis="received_ns", stamped=stamped)
    assert rate.clock_basis == "wall"
    assert rate.stamp_age_median_s == pytest.approx(0.02)
    assert rate.stamp_regression_count == 0


def test_compute_rate_without_stamped_defaults_to_none() -> None:
    rate = compute_rate([T0 + i * S for i in range(6)], basis="recorded_ns")
    assert rate.clock_basis == "none"
    assert rate.stamp_age_median_s is None
    assert rate.stamp_age_p95_s is None
    assert rate.stamp_regression_count == 0
