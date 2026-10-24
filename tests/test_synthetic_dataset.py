"""Offline generator contracts; standard library unittest, also pytest-compatible."""

import csv
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.synthetic_dataset import (
    AccountStream, DatasetConfig, OUTPUT_NAMES, build_account,
    generate_dataset, market_calendar, profile_config,
)
from src.synthetic_foundations import ANCHORS, build_anchor, score_to_bin


class ConfigurationTests(unittest.TestCase):
    def test_profiles(self):
        self.assertEqual(profile_config(), DatasetConfig())
        large = profile_config("large")
        self.assertEqual((large.accounts_per_market, large.months, large.start_period),
                         (1000, 60, "1987-01-01"))
        self.assertEqual(profile_config("large", accounts_per_market=24).accounts_per_market, 24)

    def test_invalid_configuration(self):
        for field, value in [("seed", True), ("seed", -1), ("seed", 2**63),
                             ("accounts_per_market", 10), ("accounts_per_market", 1_000_000),
                             ("accounts_per_market", True), ("months", 11),
                             ("new_market_months", 7), ("new_market_months", 13),
                             ("new_market_months", True), ("start_period", "1990-01-02"),
                             ("start_period", "0001-01-01")]:
            with self.subTest(field=field, value=value), self.assertRaises((ValueError, TypeError)):
                replace(DatasetConfig(), **{field: value})
        with self.assertRaises(ValueError):
            profile_config("huge")

    def test_calendar_and_identity_validation(self):
        self.assertEqual(market_calendar(DatasetConfig(), 3)[0], "1991-01-01")
        self.assertEqual(market_calendar(DatasetConfig(), 2)[-1], "1991-12-01")
        for market in (True, 4):
            with self.subTest(market=market), self.assertRaises(ValueError):
                market_calendar(DatasetConfig(), market)
        for ordinal in (True, 0, 25):
            with self.subTest(ordinal=ordinal), self.assertRaises(ValueError):
                build_account(DatasetConfig(), 1, ordinal)


class GenerationTests(unittest.TestCase):
    def test_fixed_anchors_are_unchanged(self):
        config = DatasetConfig()
        for market in (1, 2, 3):
            periods = market_calendar(config, market)
            for ordinal, scenario in enumerate(ANCHORS, 1):
                with self.subTest(market=market, scenario=scenario):
                    actual, metadata = build_account(config, market, ordinal)
                    self.assertEqual(actual, build_anchor(scenario, market=market,
                        start_period=periods[0], months=len(periods)))
                    self.assertEqual(metadata["kind"], "anchor")

    def test_seed_changes_background_but_not_anchors(self):
        first = DatasetConfig(); second = replace(first, seed=1730)
        self.assertEqual(build_account(first, 1, 8), build_account(second, 1, 8))
        self.assertNotEqual(build_account(first, 1, 12), build_account(second, 1, 12))

    def test_account_count_extension_preserves_existing_accounts(self):
        first = DatasetConfig(); extended = replace(first, accounts_per_market=40)
        for market in (1, 2, 3):
            for ordinal in range(1, 25):
                self.assertEqual(build_account(first, market, ordinal),
                                 build_account(extended, market, ordinal))

    def test_stream_is_independent_of_call_order_and_market(self):
        a = AccountStream(1729, 1, 12); b = AccountStream(1729, 1, 12)
        self.assertEqual([a.integer(-10, 10) for _ in range(20)],
                         [b.integer(-10, 10) for _ in range(20)])
        self.assertNotEqual(build_account(DatasetConfig(), 1, 12)[0][0].score,
                            build_account(DatasetConfig(), 3, 12)[0][0].score)

    def test_background_invariants_across_seeds(self):
        for seed in (0, 1, 42, 1729, 2**63 - 1):
            for ordinal in range(12, 20):
                config = DatasetConfig(seed=seed)
                rows, metadata = build_account(config, 1, ordinal)
                scores = [r.score for r in rows[-6:]]
                bins = [r.bin for r in rows[-6:]]
                family = metadata["scenario_id"]
                with self.subTest(seed=seed, family=family):
                    self.assertTrue(all(r.bin == score_to_bin(r.score) for r in rows))
                    self.assertEqual(len(rows), 24)
                    if family == "sudden_drop":
                        self.assertEqual((scores[2]-scores[3], bins[2]-bins[3]), (100, 2))
                    if family == "severe_drop":
                        self.assertEqual((scores[2]-scores[3], bins[2]-bins[3]), (120, 3))
                    if family == "recovery": self.assertEqual(bins[-4:], [3, 4, 4, 4])
                    if family == "stable": self.assertEqual(len(set(bins)), 1)
                    if family == "data_quality": self.assertGreater(scores[2]-scores[1], 150)
                    if family == "volatile": self.assertEqual(scores[1]-scores[0], 80)
                    if family == "improving": self.assertTrue(all(b>a for a,b in zip(scores,scores[1:])))
                    if family == "declining": self.assertTrue(all(0<a-b<40 for a,b in zip(scores,scores[1:])))

    def test_small_counts_and_metadata(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "small"
            manifest = generate_dataset(DatasetConfig(), output)
            self.assertEqual((manifest["accounts"], manifest["rows"], manifest["omitted_rows"]),
                             (72, 1380, 60))
            self.assertEqual(manifest["kind_counts"], {"anchor": 33, "background": 39})
            self.assertEqual([manifest["markets"][str(m)]["rows"] for m in (1, 2, 3)],
                             [552, 552, 276])
            self.assertEqual(manifest["market_metadata"]["3"], {
                "site_onboarded_period": "1991-01-01", "last_exception_review_period": "1991-10-01"})

    def test_large_profile_counts(self):
        with TemporaryDirectory() as temporary:
            manifest = generate_dataset(profile_config("large"), Path(temporary) / "large")
            self.assertEqual((manifest["accounts"], manifest["rows"], manifest["omitted_rows"]),
                             (3000, 131868, 132))
            self.assertEqual(manifest["scheduled_rows"], 132000)

    def test_csv_contract_and_manifest_hashes(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "small"
            manifest = generate_dataset(DatasetConfig(), output)
            content = (output / OUTPUT_NAMES[0]).read_bytes()
            self.assertTrue(content.startswith(b"account_id;period;score;bin;market\n"))
            self.assertNotIn(b"\r", content)
            self.assertNotIn(b"\xef\xbb\xbf", content)
            with (output / OUTPUT_NAMES[0]).open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream, delimiter=";"))
            self.assertEqual(rows[0]["period"], "01.01.1990")
            keys = [(int(r["market"]), int(r["account_id"]), r["period"][6:]+r["period"][3:5]) for r in rows]
            self.assertEqual(keys, sorted(set(keys)))
            for r in rows: self.assertEqual(int(r["bin"]), score_to_bin(int(r["score"])))
            for name, digest in manifest["files_sha256"].items():
                self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), digest)

    def test_all_output_bytes_repeat_across_paths(self):
        with TemporaryDirectory() as temporary:
            first, second = Path(temporary)/"a", Path(temporary)/"b"
            generate_dataset(DatasetConfig(), first)
            generate_dataset(DatasetConfig(), second)
            for name in OUTPUT_NAMES:
                self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
            manifest_text = (first / "manifest.json").read_text()
            self.assertNotIn(temporary, manifest_text)
            self.assertNotIn("created_at", manifest_text)

    def test_scenario_sidecar_agrees_with_rows(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "small"
            generate_dataset(DatasetConfig(), output)
            accounts = json.loads((output/"scenarios.json").read_text())["accounts"]
            self.assertEqual(len(accounts), 72)
            self.assertEqual(sum(a["observed_months"] for a in accounts), 1380)
            self.assertEqual(sum(len(a["omitted_periods"]) for a in accounts), 60)
            missing = next(a for a in accounts if a["account_id"] == 1_000_009)
            self.assertEqual(missing["omitted_periods"], ["1991-10-01"])
            self.assertTrue(all("decision" not in a for a in accounts))

    def test_existing_targets_are_never_overwritten(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("directory", "file"):
                target = root/name
                if name == "directory": target.mkdir()
                else: target.write_text("keep")
                with self.assertRaises(FileExistsError): generate_dataset(DatasetConfig(), target)
            self.assertEqual((root/"file").read_text(), "keep")
            self.assertEqual(list((root/"directory").iterdir()), [])

    def test_staging_failure_does_not_publish(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary)/"new"
            with patch("src.synthetic_dataset._write_staging", side_effect=ValueError("test failure")):
                with self.assertRaises(ValueError): generate_dataset(DatasetConfig(), output)
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_partial_publish_failure_cleans_own_outputs(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary)/"new"
            original = os.replace
            def fail_second(source, destination):
                if Path(destination).name == "scenarios.json": raise OSError("test failure")
                original(source, destination)
            with patch("src.synthetic_dataset.os.replace", side_effect=fail_second):
                with self.assertRaises(OSError): generate_dataset(DatasetConfig(), output)
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_cli_runs_with_only_two_generator_modules(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary); (root/"src").mkdir()
            source = Path(__file__).resolve().parents[1]/"src"
            for name in ("synthetic_foundations.py", "synthetic_dataset.py"):
                shutil.copyfile(source/name, root/"src"/name)
            env = os.environ.copy(); env.pop("PYTHONPATH", None)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            command = [sys.executable, "-m", "src.synthetic_dataset", "--output-dir", "generated"]
            result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["rows"], 1380)
            repeated = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(repeated.returncode, 2)
            self.assertIn("already exists", repeated.stderr)
            self.assertEqual(set(p.name for p in root.iterdir()), {"src", "generated"})


if __name__ == "__main__":
    unittest.main()
