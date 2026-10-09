"""Rate block and verdicts on synthetic arrival series (docs/CONTRACT.md section 4).

Every series is built from integer-nanosecond intervals so the coefficient of variation
lands exactly on the thresholds.
"""

from __future__ import annotations

import pytest

from topicforge.services.summaries import compute_rate

S = 1_000_000_000
T0 = 1_760_000_000 * S


def _series(intervals_ns: list[int]) -> list[int]:
    out = [T0]
    for step in intervals_ns:
        out.append(out[-1] + step)
    return out


def _alternating(cv_permille: int, pairs: int = 5) -> list[int]:
    """Intervals 1 s +- cv: mean exactly 1 s, population CV exactly cv, median 1 s."""
    delta = cv_permille * S // 1000
    return [S + delta, S - delta] * pairs


def _verdict(intervals_ns: list[int], **kw: object) -> str:
    return compute_rate(_series(intervals_ns), basis="received_ns", **kw).verdict  # type: ignore[arg-type]


# ---- order of evaluation ----------------------------------------------------


def test_no_message_is_silent() -> None:
    rate = compute_rate([], basis="received_ns", window_start_ns=T0, window_end_ns=T0 + 10 * S)
    assert rate.verdict == "silent"
    assert rate.message_count == 0 and rate.window_s == 10.0
    assert rate.observed_frequency_hz is None and rate.interval_cv is None
    assert rate.trailing_gap_s is None
    assert "10.0 s" in rate.verdict_note


@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_fewer_than_five_messages_are_insufficient(n: int) -> None:
    rate = compute_rate(_series([S] * (n - 1)), basis="received_ns")
    assert rate.verdict == "insufficient"
    assert "count >= 10" in rate.verdict_note


def test_five_messages_are_enough() -> None:
    assert _verdict([S] * 4) == "stable"


def test_insufficient_wins_over_a_huge_gap() -> None:
    # 4 messages with a 100 s hole: still too few to judge.
    assert _verdict([S, 100 * S, S]) == "insufficient"


def test_intermittent_wins_over_a_low_cv() -> None:
    # One long gap after steady delivery. The cv here is high too; the order decides.
    assert _verdict([S] * 8 + [4 * S]) == "intermittent"


# ---- the 3x median gap boundary ---------------------------------------------


def test_a_gap_of_exactly_three_medians_is_not_intermittent() -> None:
    assert _verdict([S] * 9 + [3 * S]) != "intermittent"


def test_a_gap_just_over_three_medians_is_intermittent() -> None:
    assert _verdict([S] * 9 + [3 * S + 1]) == "intermittent"


def test_the_gap_can_be_anywhere_in_the_series() -> None:
    assert _verdict([S] * 4 + [10 * S] + [S] * 4) == "intermittent"


def test_intermittent_note_names_the_gap() -> None:
    rate = compute_rate(_series([S] * 5 + [5 * S] + [S] * 4), basis="received_ns")
    assert rate.max_gap_s == 5.0
    assert "5.00 s" in rate.verdict_note and "median interval of 1.000 s" in rate.verdict_note


# ---- the trailing gap -------------------------------------------------------


def test_trailing_gap_counts_when_collection_stopped_on_the_deadline() -> None:
    arrivals = _series([S] * 9)
    end = arrivals[-1] + 4 * S
    rate = compute_rate(
        arrivals,
        basis="received_ns",
        window_start_ns=T0,
        window_end_ns=end,
        stopped_on_deadline=True,
    )
    assert rate.verdict == "intermittent"
    assert rate.trailing_gap_s == 4.0
    assert "after the last message" in rate.verdict_note


def test_trailing_gap_of_exactly_three_medians_is_not_intermittent() -> None:
    arrivals = _series([S] * 9)
    rate = compute_rate(
        arrivals,
        basis="received_ns",
        window_end_ns=arrivals[-1] + 3 * S,
        stopped_on_deadline=True,
    )
    assert rate.verdict == "stable" and rate.trailing_gap_s == 3.0


def test_trailing_gap_is_ignored_when_collection_stopped_on_count() -> None:
    arrivals = _series([S] * 9)
    rate = compute_rate(
        arrivals,
        basis="received_ns",
        window_end_ns=arrivals[-1] + 60 * S,
        stopped_on_deadline=False,
    )
    assert rate.verdict == "stable" and rate.trailing_gap_s is None


def test_a_normal_trailing_interval_does_not_trip_the_verdict() -> None:
    arrivals = _series([S] * 9)
    rate = compute_rate(
        arrivals,
        basis="received_ns",
        window_end_ns=arrivals[-1] + S // 2,
        stopped_on_deadline=True,
    )
    assert rate.verdict == "stable" and rate.trailing_gap_s == 0.5


# ---- the cv boundaries ------------------------------------------------------


@pytest.mark.parametrize(
    ("cv_permille", "expected"),
    [
        (0, "stable"),
        (199, "stable"),
        (200, "jittery"),
        (499, "jittery"),
        (500, "erratic"),
        (900, "erratic"),
    ],
)
def test_cv_thresholds(cv_permille: int, expected: str) -> None:
    # Intervals 1 +- cv never exceed 1.9 s, below the 3x median gap rule.
    assert _verdict(_alternating(cv_permille)) == expected


def test_the_cv_of_the_test_series_is_what_it_claims() -> None:
    rate = compute_rate(_series(_alternating(200)), basis="received_ns")
    assert rate.interval_cv == pytest.approx(0.2, abs=1e-9)
    assert rate.interval_median_s == pytest.approx(1.0)


def test_verdict_notes_are_one_sentence_each() -> None:
    for intervals in ([S] * 9, _alternating(300), _alternating(700)):
        note = compute_rate(_series(intervals), basis="received_ns").verdict_note
        assert note.endswith(".") and note.count(". ") == 0


def test_erratic_note_mentions_multiple_publishers() -> None:
    note = compute_rate(_series(_alternating(700)), basis="received_ns").verdict_note
    assert "several publishers" in note and "/tf" in note


def test_jittery_note_states_the_gap_ratio() -> None:
    note = compute_rate(_series(_alternating(300)), basis="received_ns").verdict_note
    assert "1.3 times the median" in note and "healthy" in note


# ---- the numbers ------------------------------------------------------------


def test_steady_five_hertz() -> None:
    rate = compute_rate(_series([S // 5] * 19), basis="received_ns")
    assert rate.verdict == "stable"
    assert rate.observed_frequency_hz == pytest.approx(5.0)
    assert rate.mean_interval_s == pytest.approx(0.2)
    assert rate.interval_median_s == pytest.approx(0.2)
    assert rate.max_gap_s == pytest.approx(0.2)
    assert rate.interval_cv == 0.0
    assert rate.window_s == pytest.approx(3.8)
    assert rate.message_count == 20


def test_unsorted_arrivals_are_ordered() -> None:
    arrivals = _series([S] * 9)
    assert compute_rate(arrivals[::-1], basis="received_ns").verdict == "stable"


def test_a_burst_with_one_arrival_time_is_erratic_without_a_frequency() -> None:
    rate = compute_rate([T0] * 6, basis="received_ns")
    assert rate.verdict == "erratic"
    assert rate.observed_frequency_hz is None and rate.interval_cv is None
    assert "burst" in rate.verdict_note


def test_window_defaults_to_the_span_of_the_arrivals() -> None:
    rate = compute_rate(_series([S] * 9), basis="recorded_ns")
    assert rate.window_s == 9.0 and rate.basis == "recorded_ns"


# ---- the simulated-clock frequency ------------------------------------------


def test_sim_frequency_comes_from_the_message_stamps() -> None:
    arrivals = _series([S] * 9)  # 1 Hz on the wall
    stamps = [i * S // 2 for i in range(10)]  # 2 Hz on the publisher's clock
    rate = compute_rate(arrivals, basis="received_ns", stamps_ns=stamps)
    assert rate.observed_frequency_hz == pytest.approx(1.0)
    assert rate.sim_frequency_hz == pytest.approx(2.0)


def test_sim_frequency_is_null_without_stamps_or_with_frozen_ones() -> None:
    arrivals = _series([S] * 9)
    assert compute_rate(arrivals, basis="received_ns").sim_frequency_hz is None
    frozen = compute_rate(arrivals, basis="received_ns", stamps_ns=[0] * 10)
    assert frozen.sim_frequency_hz is None


# ---- start-up burst of ros2 topic echo --------------------------------------

MS = S // 1000


def test_the_echo_drain_is_set_aside_on_received_ns() -> None:
    rate = compute_rate(_series([MS] * 5 + [20 * MS] * 6), basis="received_ns")
    assert rate.startup_burst_count == 5
    assert rate.message_count == 12
    assert rate.observed_frequency_hz == 50.0
    assert rate.interval_cv == 0.0
    assert rate.verdict == "stable"


def test_the_drain_is_kept_on_recorded_ns() -> None:
    rate = compute_rate(_series([MS] * 5 + [20 * MS] * 6), basis="recorded_ns")
    assert rate.startup_burst_count == 0
    assert rate.observed_frequency_hz == pytest.approx(11 / 0.125)


def test_drained_messages_still_feed_the_sim_frequency() -> None:
    arrivals = _series([MS] * 5 + [20 * MS] * 6)
    stamps = [i * 20 * MS for i in range(len(arrivals))]
    rate = compute_rate(arrivals, basis="received_ns", stamps_ns=stamps)
    assert rate.startup_burst_count == 5
    assert rate.sim_frequency_hz == 50.0


def test_a_burst_covering_over_half_the_series_is_not_a_drain() -> None:
    rate = compute_rate(_series([MS] * 8 + [20 * MS] * 3), basis="received_ns")
    assert rate.startup_burst_count == 0


def test_a_burst_in_the_middle_is_not_a_drain() -> None:
    rate = compute_rate(_series([20 * MS] * 4 + [MS] * 3 + [20 * MS] * 4), basis="received_ns")
    assert rate.startup_burst_count == 0
    assert rate.verdict != "stable"


def test_fewer_than_four_intervals_are_never_trimmed() -> None:
    rate = compute_rate(_series([MS, MS, 20 * MS]), basis="received_ns")
    assert rate.startup_burst_count == 0


def test_identical_arrivals_are_not_a_drain() -> None:
    assert compute_rate([T0] * 5, basis="received_ns").startup_burst_count == 0


def test_setting_the_drain_aside_can_leave_too_few_messages() -> None:
    rate = compute_rate(_series([MS] * 2 + [20 * MS] * 3), basis="received_ns")
    assert rate.startup_burst_count == 2
    assert rate.verdict == "insufficient"
