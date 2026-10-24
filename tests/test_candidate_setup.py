"""Required setup integration: verified inputs and fail-closed rebuild behavior."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


class CandidateSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "src").mkdir()
        source = Path(__file__).resolve().parents[1] / "src"
        for name in ("__init__.py", "data_loader.py", "synthetic_dataset.py",
                     "synthetic_database.py", "synthetic_foundations.py"):
            shutil.copyfile(source / name, self.root / "src" / name)

    def setup_command(self):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run([sys.executable, "-m", "src.data_loader"],
                              cwd=self.root, env=env, capture_output=True, text=True)

    def test_absent_inputs_generate_verified_database_and_receipt(self):
        result = self.setup_command()
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads((self.root / "data/database_receipt.json").read_text())
        self.assertEqual((receipt["accounts"], receipt["rows"]), (72, 1380))
        self.assertTrue((self.root / "data/scores.db").is_file())

    def test_modified_csv_cannot_replace_existing_database(self):
        self.assertEqual(self.setup_command().returncode, 0)
        data = self.root / "data"
        before = (data / "scores.db").read_bytes()
        csv = data / "scores_extended.csv"
        csv.write_bytes(csv.read_bytes().replace(b";345;7;1", b";346;7;1", 1))
        result = self.setup_command()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match", result.stderr)
        self.assertEqual((data / "scores.db").read_bytes(), before)

    def test_partial_input_bundle_fails_without_replacing_database(self):
        data = self.root / "data"
        data.mkdir()
        (data / "scores_extended.csv").write_text("incomplete")
        (data / "scores.db").write_bytes(b"previous database sentinel")
        result = self.setup_command()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Incomplete synthetic input", result.stderr)
        self.assertEqual((data / "scores.db").read_bytes(), b"previous database sentinel")


if __name__ == "__main__": unittest.main()
