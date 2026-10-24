"""Tests for independent temporal-fact validation (Module 5.3.1)."""
import csv
from pathlib import Path
import pytest

from src.temporal_policy import evaluate_temporal_signals


def make_history(scores, bins=None, start_year=2024, start_month=7):
    from datetime import date
    bins = bins or [6] * len(scores)
    return [
        {"period": date((start_year * 12 + start_month - 1 + i) // 12,
                        (start_year * 12 + start_month - 1 + i) % 12 + 1, 1).isoformat(),
         "score": score, "bin": b, "market": 1}
        for i, (score, b) in enumerate(zip(scores, bins))
    ]


def test_three_declines_require_four_observations():
    history = make_history([600, 600, 600, 600, 600, 590, 580, 570])
    result = evaluate_temporal_signals(history, "2025-02-01")
    assert result["events"]["three_declines"] == [{"period": "2025-02-01"}]
    assert result["computed_signal_status"] == "triggered"


def test_equal_scores_break_the_decline_streak():
    history = make_history([600, 600, 600, 600, 600, 590, 590, 570])
    result = evaluate_temporal_signals(history, "2025-02-01")
    assert result["events"]["three_declines"] == []
    assert result["computed_signal_status"] == "not_triggered"


def test_40_point_drop_and_two_bin_drop_are_inclusive():
    history = make_history([600] * 7 + [560], bins=[6] * 7 + [4])
    result = evaluate_temporal_signals(history, "2025-02-01")
    assert result["events"]["score_drop_40"][0]["score_change"] == -40
    assert result["events"]["down_2_bins"][0]["bin_change"] == -2


def test_three_month_window_ignores_old_trigger():
    history = make_history([600, 550, 550, 550, 550, 550, 550, 550])
    result = evaluate_temporal_signals(history, "2025-02-01")
    assert result["events"]["score_drop_40"] == []
    assert result["computed_signal_status"] == "not_triggered"


def test_gaps_do_not_count_as_consecutive_declines():
    history = make_history([600, 600, 600, 600, 600, 590, 580, 570])
    history = [row for row in history if row["period"] != "2025-01-01"]
    result = evaluate_temporal_signals(history, "2025-02-01")
    assert result["events"]["three_declines"] == []
    assert result["computed_signal_status"] == "insufficient_evidence"
    assert result["missing_baseline_months"] == ["2025-01-01"]


def test_as_of_rejects_future_only_or_stale_history():
    with pytest.raises(ValueError, match="as-of month"):
        evaluate_temporal_signals(make_history([600, 590]), "2025-02-01")


def test_real_account_81000001_march_streak_still_inside_april_window():
    source = Path(__file__).resolve().parents[1] / "data" / "scores_extended.csv"
    if not source.exists():
        pytest.fail("Required generated CSV missing; run python -m src.data_loader")
    with source.open(encoding="utf-8", newline="") as file:
        rows = [r for r in csv.DictReader(file, delimiter=";")
                if int(r["account_id"]) == 1000003]
    # Project input CSV is DD.MM.YYYY, while database get_history returns ISO.
    from datetime import datetime
    history = [{"period": datetime.strptime(r["period"], "%d.%m.%Y").date().isoformat(),
                "score": int(r["score"]), "bin": int(r["bin"])}
               for r in rows]
    result = evaluate_temporal_signals(history, "1991-12-01")
    assert [e["period"] for e in result["events"]["three_declines"]] == [
        "1991-10-01", "1991-11-01", "1991-12-01"
    ]
    assert result["computed_signal_status"] == "triggered"


def test_large_six_period_volatility_is_detected():
    history = make_history([600, 600, 600, 600, 600, 600, 700, 700])
    result = evaluate_temporal_signals(history, "2025-02-01")
    assert result["events"]["volatility_over_35"]
    assert all(e["stdev"] > 35 for e in result["events"]["volatility_over_35"])


def test_only_six_observations_cannot_prove_no_three_month_window_signal():
    history = make_history([600, 600, 600, 600, 600, 600], start_month=9)
    result = evaluate_temporal_signals(history, "2025-02-01")
    assert result["computed_signal_status"] == "insufficient_evidence"
    assert not result["coverage_complete"]
