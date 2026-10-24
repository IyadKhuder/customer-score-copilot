"""Offline tests runnable with unittest or pytest; no third-party dependencies."""

import dataclasses
from datetime import date
from statistics import pstdev
import unittest

from src.synthetic_foundations import (
    ANCHORS, anchor_account_id, build_anchor, build_default_anchors,
    monthly_calendar, score_to_bin,
)


class MappingTests(unittest.TestCase):
    def test_every_bin_has_exactly_fifty_scores(self):
        counts = {number: 0 for number in range(1, 10)}
        for score in range(450):
            counts[score_to_bin(score)] += 1
        self.assertEqual(list(counts.values()), [50] * 9)

    def test_edges_and_neighbors(self):
        self.assertEqual(score_to_bin(0), 1)
        self.assertEqual(score_to_bin(449), 9)
        for edge, expected in [(50, 2), (100, 3), (150, 4), (200, 5),
                               (250, 6), (300, 7), (350, 8), (400, 9)]:
            with self.subTest(edge=edge):
                self.assertEqual(score_to_bin(edge - 1), expected - 1)
                self.assertEqual(score_to_bin(edge), expected)
                self.assertEqual(score_to_bin(edge + 1), expected)

    def test_mapping_is_monotone(self):
        bins = [score_to_bin(s) for s in range(450)]
        self.assertEqual(bins, sorted(bins))

    def test_out_of_range_scores_are_rejected(self):
        for value in (-1, 450, 1000):
            with self.subTest(value=value), self.assertRaises(ValueError):
                score_to_bin(value)

    def test_coercion_and_booleans_are_rejected(self):
        for value in (True, False, 1.0, "50", None):
            with self.subTest(value=value), self.assertRaises(TypeError):
                score_to_bin(value)


class CalendarTests(unittest.TestCase):
    def test_fictional_default_calendar(self):
        periods = monthly_calendar("1990-01-01", 24)
        self.assertEqual(len(periods), 24)
        self.assertEqual(periods[0], "1990-01-01")
        self.assertEqual(periods[-1], "1991-12-01")

    def test_leap_year_and_year_rollover(self):
        self.assertEqual(monthly_calendar("1991-12-01", 4),
                         ("1991-12-01", "1992-01-01", "1992-02-01", "1992-03-01"))

    def test_month_starts_are_contiguous(self):
        periods = monthly_calendar("1987-01-01", 60)
        parsed = [date.fromisoformat(p) for p in periods]
        indices = [d.year * 12 + d.month for d in parsed]
        self.assertTrue(all(d.day == 1 for d in parsed))
        self.assertTrue(all(b - a == 1 for a, b in zip(indices, indices[1:])))

    def test_noncanonical_and_invalid_dates_are_rejected(self):
        for value in ("19900101", "1990-01-02", "1990-13-01", "1990-1-01", "junk"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                monthly_calendar(value, 12)
        with self.assertRaises(TypeError):
            monthly_calendar(date(1990, 1, 1), 12)

    def test_invalid_lengths_are_rejected(self):
        for value in (0, -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                monthly_calendar("1990-01-01", value)
        for value in (True, 12.0, "12"):
            with self.subTest(value=value), self.assertRaises(TypeError):
                monthly_calendar("1990-01-01", value)

    def test_calendar_limits(self):
        self.assertEqual(monthly_calendar("0001-01-01", 1), ("0001-01-01",))
        self.assertEqual(monthly_calendar("9999-12-01", 1), ("9999-12-01",))
        with self.assertRaises(ValueError):
            monthly_calendar("9999-12-01", 2)


class AnchorTests(unittest.TestCase):
    def test_stable_ids_and_global_uniqueness(self):
        self.assertEqual(anchor_account_id("stable"), 1_000_001)
        self.assertEqual(anchor_account_id("short_history", 3), 3_000_011)
        ids = [anchor_account_id(s, m) for m in (1, 2, 3) for s in ANCHORS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_invalid_identity_is_rejected(self):
        for scenario, market, error in [("unknown", 1, ValueError),
                                         ("stable", 4, ValueError),
                                         ("stable", True, TypeError),
                                         ("stable", 1.0, TypeError),
                                         (None, 1, TypeError)]:
            with self.subTest(scenario=scenario, market=market), self.assertRaises(error):
                build_anchor(scenario, market=market)

    def test_too_short_calendar_is_rejected(self):
        for scenario in ANCHORS:
            with self.subTest(scenario=scenario), self.assertRaises(ValueError):
                build_anchor(scenario, months=7)

    def test_sudden_and_severe_drops_are_distinct(self):
        sudden = build_anchor("sudden_drop")
        severe = build_anchor("severe_drop")
        self.assertEqual(sudden[-4].score - sudden[-3].score, 100)
        self.assertEqual(sudden[-4].bin - sudden[-3].bin, 2)
        self.assertEqual(severe[-4].score - severe[-3].score, 120)
        self.assertEqual(severe[-4].bin - severe[-3].bin, 3)
        self.assertEqual(severe[-3].period, "1991-10-01")

    def test_recovery_has_three_observations_in_standard(self):
        rows = build_anchor("recovery")
        self.assertEqual([r.bin for r in rows[-4:]], [3, 4, 4, 4])

    def test_volatility_and_exact_reversal(self):
        scores = [r.score for r in build_anchor("volatile")[-6:]]
        self.assertEqual(pstdev(scores), 40)
        self.assertEqual(scores[0], scores[2])
        quality = build_anchor("data_quality")
        self.assertEqual(quality[-4].score - quality[-5].score, 190)
        self.assertEqual(quality[-5].score, quality[-3].score)

    def test_declining_has_five_small_transitions(self):
        scores = [r.score for r in build_anchor("declining")[-6:]]
        self.assertEqual([b - a for a, b in zip(scores, scores[1:])], [-12] * 5)

    def test_coverage_omissions_are_exact(self):
        interior = build_anchor("missing_interior")
        latest = build_anchor("missing_latest")
        short = build_anchor("short_history")
        self.assertEqual(len(interior), 23)
        self.assertNotIn("1991-10-01", [r.period for r in interior])
        self.assertEqual(interior[-1].period, "1991-12-01")
        self.assertEqual(len(latest), 23)
        self.assertEqual(latest[-1].period, "1991-11-01")
        self.assertEqual([r.period for r in short], ["1991-11-01", "1991-12-01"])

    def test_longer_calendar_preserves_tail_and_ids(self):
        short = build_anchor("severe_drop")
        long = build_anchor("severe_drop", start_period="1987-01-01", months=60)
        self.assertEqual(short[-6:], long[-6:])

    def test_all_rows_are_valid_unique_and_sorted(self):
        rows = build_default_anchors()
        keys = [(r.market, r.account_id, r.period) for r in rows]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len({(r.account_id, r.period) for r in rows}), len(rows))
        for row in rows:
            self.assertEqual(row.bin, score_to_bin(row.score))
            self.assertEqual(set(row.as_dict()), {"account_id", "period", "score", "bin", "market"})

    def test_default_anchor_counts(self):
        rows = build_default_anchors()
        self.assertEqual(len(rows), 600)
        self.assertEqual(len({r.account_id for r in rows}), 33)
        self.assertEqual([sum(r.market == m for r in rows) for m in (1, 2, 3)],
                         [240, 240, 120])

    def test_values_are_immutable_and_export_is_a_copy(self):
        row = build_anchor("stable")[0]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            row.score = 0
        with self.assertRaises(TypeError):
            ANCHORS["new"] = ANCHORS["stable"]
        exported = row.as_dict()
        exported["score"] = 0
        self.assertEqual(row.score, 345)

    def test_repeated_construction_is_identical(self):
        self.assertEqual(build_default_anchors(), build_default_anchors())


if __name__ == "__main__":
    unittest.main()
