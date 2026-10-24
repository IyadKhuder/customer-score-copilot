"""Offline tests; synthetic project tree avoids importing config or any SDK."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from src.experiment_metadata import REQUIRED, build_experiment_metadata, save_experiment_metadata
from scripts.prepare_experiment_metadata_6522 import main


class ExperimentMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in REQUIRED:
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('{}' if p.suffix == '.json' else '# synthetic source\n', encoding='utf-8')
        (self.root / 'src/config.py').write_text(
            "raise RuntimeError('CONFIG MUST NOT EXECUTE')\nPROVIDERS = {"
            "'gemini': {'adapter': 'openai_compatible', 'model': 'configured-g', 'api_key': 'SECRET'},"
            "'anthropic': {'adapter': 'anthropic', 'model': 'configured-a', 'api_key_env': 'SECRET_ENV'}}\n",
            encoding='utf-8')

    def build(self, **kwargs):
        return build_experiment_metadata(self.root, provider='gemini', **kwargs)

    def test_allowlist_and_no_config_execution(self):
        m = self.build()
        text = json.dumps(m)
        self.assertNotIn('SECRET', text)
        self.assertNotIn('api_key', text)
        self.assertEqual(m['provider_configuration']['configured_model'], 'configured-g')
        self.assertFalse(m['execution_verified'])
        self.assertIsNone(m['execution_record'])
        self.assertIsNone(m['provider_configuration']['served_model'])

    def test_both_provider_configurations(self):
        m = build_experiment_metadata(self.root, provider='anthropic')
        self.assertEqual(m['provider_configuration']['configured_model'], 'configured-a')
        self.assertEqual(m['provider_configuration']['adapter'], 'anthropic')

    def test_settings_are_planned_and_defaults_explicit(self):
        m = self.build(between_case_seconds=20, max_rate_limit_retries=2)
        self.assertEqual(m['record_kind'], 'preflight_plan')
        self.assertTrue(m['planned_settings']['policy_tool_suite'])
        self.assertEqual(m['planned_settings']['between_case_seconds'], 20.0)
        self.assertEqual(m['planned_settings']['max_rate_limit_retries'], 2)
        self.assertIsNone(m['planned_settings']['temperature']['value'])

    def test_hashes_detect_source_and_prompt_changes(self):
        before = self.build()
        p = self.root / 'src/agent.py'
        p.write_text('PROMPT = "changed"\n', encoding='utf-8')
        after = self.build()
        self.assertNotEqual(before['source_snapshot_sha256'], after['source_snapshot_sha256'])
        self.assertEqual(after['source_files_sha256']['src/agent.py'], hashlib.sha256(p.read_bytes()).hexdigest())

    def test_extra_src_file_included_env_excluded(self):
        (self.root / 'src/verification_pipeline.py').write_text('# verifier\n')
        (self.root / '.env').write_text('KEY=PRIVATE')
        m = self.build()
        self.assertIn('src/verification_pipeline.py', m['source_files_sha256'])
        self.assertNotIn('.env', m['source_files_sha256'])
        self.assertNotIn('PRIVATE', json.dumps(m))

    def test_canonical_evaluation_fingerprints(self):
        p = self.root / 'evaluation/benchmark_cases_synthetic_v2.json'
        p.write_text('{"b": 2, "a": 1}')
        first = self.build()
        p.write_text('{\n "a":1, "b":2\n}')
        second = self.build()
        self.assertEqual(first['evaluation_fingerprints'], second['evaluation_fingerprints'])
        p.write_text('{"a":1, "b":3}')
        self.assertNotEqual(second['evaluation_fingerprints'], self.build()['evaluation_fingerprints'])

    def test_missing_required_source_fails(self):
        (self.root / 'src/agent.py').unlink()
        with self.assertRaises(FileNotFoundError): self.build()

    def test_invalid_parameters(self):
        for kwargs in ({'between_case_seconds':float('nan')}, {'retry_delay_seconds':float('inf')},
                       {'between_case_seconds':True}, {'max_tool_rounds':True},
                       {'max_rate_limit_retries':4}, {'policy_passages':'invented'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): self.build(**kwargs)
        with self.assertRaises(ValueError): build_experiment_metadata(self.root, provider='unknown')

    def test_dynamic_config_not_evaluated(self):
        (self.root / 'src/config.py').write_text('PROVIDERS = get_secrets()\n')
        with self.assertRaises(ValueError): self.build()

    def test_save_exclusive_and_no_mutation(self):
        m = self.build()
        original = copy.deepcopy(m)
        p = self.root / 'plan.json'
        save_experiment_metadata(m, p)
        data = p.read_bytes()
        with self.assertRaises(FileExistsError): save_experiment_metadata(m, p)
        self.assertEqual(p.read_bytes(), data)
        self.assertEqual(m, original)

    def test_cli_offline_plan(self):
        destination = self.root / 'plan.json'
        rc = main(['--project-root', str(self.root), '--provider', 'anthropic', '--output', str(destination)])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(destination.read_text())['record_kind'], 'preflight_plan')

    def test_root_escape_symlink_rejected(self):
        other = self.root / 'outside'
        other.mkdir()
        nested = self.root / 'project'
        nested.mkdir()
        for p in REQUIRED:
            f = nested / p
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text('{}' if f.suffix == '.json' else '# source')
        target = other / 'agent.py'
        target.write_text('# external')
        (nested / 'src/agent.py').unlink()
        try:
            (nested / 'src/agent.py').symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest('Symlink creation unavailable on this platform')
        with self.assertRaises(ValueError): build_experiment_metadata(nested, provider='gemini')
