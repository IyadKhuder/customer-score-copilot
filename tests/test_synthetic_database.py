"""Strict loading into a separate SQLite database; no application imports."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.synthetic_dataset import DatasetConfig, generate_dataset
from src.synthetic_database import build_database, sha256


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dataset = self.root / "dataset"
        self.output = self.root / "database"
        generate_dataset(DatasetConfig(), self.dataset)

    def test_load_counts_dates_and_input_provenance(self):
        receipt = build_database(self.dataset, self.output)
        self.assertEqual((receipt["rows"], receipt["accounts"]), (1380, 72))
        self.assertEqual((receipt["first_period"], receipt["last_period"]),
                         ("1990-01-01", "1991-12-01"))
        self.assertEqual(receipt["database_sha256"], sha256(self.output / "scores.db"))
        for name, digest in receipt["input_sha256"].items():
            self.assertEqual(sha256(self.dataset / name), digest)
        with closing(sqlite3.connect(self.output / "scores.db")) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            self.assertEqual(conn.execute(
                "SELECT score,bin FROM scores WHERE account_id=1000008 AND period='1991-10-01'"
            ).fetchone(), (239, 5))

    def test_tampered_csv_is_rejected_even_after_updating_manifest_hash(self):
        path = self.dataset / "scores_extended.csv"
        path.write_bytes(path.read_bytes().replace(b";345;7;1", b";346;7;1", 1))
        manifest_path = self.dataset / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files_sha256"]["scores_extended.csv"] = sha256(path)
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError): build_database(self.dataset, self.output)
        self.assertFalse(self.output.exists())

    def test_changed_sidecar_is_rejected(self):
        path = self.dataset / "scenarios.json"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaises(ValueError): build_database(self.dataset, self.output)
        self.assertFalse(self.output.exists())

    def test_changed_source_identity_is_rejected(self):
        path = self.dataset / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["source_sha256"]["synthetic_dataset.py"] = "0" * 64
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError): build_database(self.dataset, self.output)

    def test_existing_target_is_preserved(self):
        self.output.mkdir()
        sentinel = self.output / "keep.txt"
        sentinel.write_text("keep")
        with self.assertRaises(FileExistsError): build_database(self.dataset, self.output)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_missing_input_does_not_create_database(self):
        (self.dataset / "scenarios.json").unlink()
        with self.assertRaises(ValueError): build_database(self.dataset, self.output)
        self.assertFalse(self.output.exists())

    def test_schema_rejects_inconsistent_bins_and_duplicate_keys(self):
        build_database(self.dataset, self.output)
        with closing(sqlite3.connect(self.output / "scores.db")) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO scores VALUES (999,'1991-12-01',345,1,1)")
            conn.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO scores SELECT * FROM scores LIMIT 1")
            conn.rollback()

    def test_writer_connection_is_closed_before_publication(self):
        original = sqlite3.connect
        connections = []
        def track(*args, **kwargs):
            conn = original(*args, **kwargs)
            connections.append(conn)
            return conn
        with patch("src.synthetic_database.sqlite3.connect", side_effect=track):
            build_database(self.dataset, self.output)
        self.assertTrue(connections)
        for conn in connections:
            with self.assertRaises(sqlite3.ProgrammingError): conn.execute("SELECT 1")

    def test_input_dataset_is_not_modified(self):
        before = {p.name: p.read_bytes() for p in self.dataset.iterdir()}
        build_database(self.dataset, self.output)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.dataset.iterdir()})


if __name__ == "__main__":
    unittest.main()
