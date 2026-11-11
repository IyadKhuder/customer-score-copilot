"""Deterministic release/retry checks; no Docker, network or provider access."""
import importlib.util
import io
import json
from pathlib import Path
import ssl
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
import tempfile
import tarfile
import hashlib

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'deploy' / 'azure' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


client = module('submit_case')
release = module('release')


class Response(io.BytesIO):
    status = 202


class DeliveryTests(unittest.TestCase):
    def test_lost_response_reuses_durable_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'intent.json'
            saved = client.intent('https://example.org', path)
            self.assertEqual(saved, client.intent('https://example.org/', path))
            calls = []
            cases = {}
            result = dict(case_id='12345678-1234-4234-8234-123456789abc', account_ids=[1000003],
                          market=1, as_of_period='1991-12-01', workflow_status='queued')

            class Opener:
                def open(self, request, timeout):
                    key = request.get_header('Idempotency-key')
                    calls.append((key, request.data))
                    cases.setdefault(key, result)
                    if len(calls) == 1:
                        raise TimeoutError()  # Simulated server save followed by lost response.
                    return Response(json.dumps(cases[key]).encode())
            waits = []
            self.assertEqual(client.submit(saved, 'x' * 32, Opener(), waits.append), result)
            self.assertEqual(len(cases), 1)
            self.assertEqual(calls[0], calls[1])
            self.assertEqual(waits, [1])

    def test_retry_exhaustion_has_three_attempts(self):
        calls, waits = [], []
        class Opener:
            def open(self, *args, **kwargs):
                calls.append(1)
                raise URLError('temporary connection failure')
        saved = dict(origin='https://example.org', key='same', payload=client.BODY)
        with self.assertRaisesRegex(RuntimeError, 'outcome unknown'):
            client.submit(saved, 'x' * 32, Opener(), waits.append)
        self.assertEqual(len(calls), 3)
        self.assertEqual(waits, [1, 2])

    def test_http_failures_and_tls_policy(self):
        for code in (401, 409, 422, 500, 502, 503, 504):
            calls = []
            class Opener:
                def open(self, *args, **kwargs):
                    calls.append(1)
                    raise HTTPError('https://example.org', code, 'failure', {}, None)
            with self.assertRaises(RuntimeError):
                client.submit(dict(origin='https://example.org', key='same', payload=client.BODY),
                              'x' * 32, Opener(), lambda _: None)
            self.assertEqual(len(calls), 3 if code in client.RETRYABLE else 1)
        class TLS:
            def open(self, *args, **kwargs):
                raise URLError(ssl.SSLCertVerificationError('untrusted'))
        with self.assertRaisesRegex(RuntimeError, 'TLS failed'):
            client.submit(dict(origin='https://example.org', key='same', payload=client.BODY),
                          'x' * 32, TLS(), lambda _: self.fail('TLS must not retry'))

    def test_intent_cannot_silently_change_origin_or_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'intent.json'
            saved = client.intent('https://example.org', path)
            with self.assertRaises(ValueError):
                client.intent('https://other.example.org', path)
            saved['payload']['account_id'] = 1
            path.write_text(json.dumps(saved))
            with self.assertRaises(ValueError):
                client.intent('https://example.org', path)
        for base in ('http://remote.example.org', 'https://user:secret@example.org', 'https://example.org/path'):
            with self.assertRaises(ValueError):
                client.origin(base)

    def test_manifest_and_immutable_override(self):
        value = dict(schema=1, source_commit='a' * 40, image_id='sha256:' + 'b' * 64,
                     archive_sha256='c' * 64, implementation={n: 'd' * 64 for n in release.FILES})
        self.assertEqual(release.validate(value), value)
        self.assertEqual(release.override(value['image_id'], value['image_id'])['services']['api']['pull_policy'], 'never')
        with self.assertRaises(ValueError):
            release.override('scorecopilot:latest', value['image_id'])
        value['image_id'] = 'mutable:tag'
        with self.assertRaises(ValueError):
            release.validate(value)

    def test_pack_refuses_dirty_checkout_before_docker(self):
        with patch.object(release, 'command', return_value=' M tracked.py') as command:
            with self.assertRaisesRegex(ValueError, 'Commit tracked'):
                release.pack('unused', Path('unused'))
            self.assertEqual(command.call_count, 1)


class ReleaseFailureTests(unittest.TestCase):
    def fixture(self, directory):
        archive = directory / 'app-image.tar'
        archive.write_bytes(b'test image archive')
        manifest = dict(schema=1, source_commit='a' * 40, image_id='sha256:' + 'b' * 64,
                        archive_sha256=release.checksum(archive),
                        implementation={n: 'd' * 64 for n in release.FILES})
        (directory / 'release.json').write_text(json.dumps(manifest))
        return manifest

    def test_archive_corruption_stops_before_load(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            self.fixture(directory)
            (directory / 'app-image.tar').write_bytes(b'corrupted')
            with patch.object(release, 'command') as command:
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    release.deployment_plan(directory, 'unused', 'unused')
                command.assert_not_called()

    def test_compatible_release_retains_previous_ids_and_rejects_changed_code(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            manifest = self.fixture(directory)
            previous, proxy = 'sha256:' + 'e' * 64, 'sha256:' + 'f' * 64
            info = dict(Id=manifest['image_id'], Architecture='amd64', Os='linux',
                        Config={'Labels': {'org.opencontainers.image.revision': manifest['source_commit']}})
            with patch.object(release, 'load_verified_image', return_value=(info, manifest['image_id'])), \
                    patch.object(release, 'fingerprints', return_value=manifest['implementation']):
                receipt = release.deployment_plan(directory, previous, proxy)
                self.assertEqual(receipt['previous'], release.override(previous, proxy))
                self.assertEqual(receipt['candidate'], release.override(manifest['image_id'], proxy))
            with patch.object(release, 'load_verified_image', return_value=(info, manifest['image_id'])), \
                    patch.object(release, 'fingerprints', side_effect=[manifest['implementation'], {}]):
                with self.assertRaisesRegex(ValueError, 'Implementation changed'):
                    release.deployment_plan(directory, previous, proxy)
            self.assertFalse((directory / 'activation.json').exists())

    def test_rollback_refuses_unexpected_active_image_or_changed_code(self):
        previous, candidate, proxy = ('sha256:' + c * 64 for c in ('a', 'b', 'c'))
        receipt = dict(previous=release.override(previous, proxy), candidate=release.override(candidate, proxy))
        with self.assertRaisesRegex(ValueError, 'Running image differs'):
            release.rollback_plan(receipt, previous)
        with patch.object(release, 'fingerprints', side_effect=[{}, {'changed': True}]):
            with self.assertRaisesRegex(ValueError, 'compatibility changed'):
                release.rollback_plan(receipt, candidate)
        with patch.object(release, 'fingerprints', return_value={}), patch.object(release, 'inspect'):
            self.assertEqual(release.rollback_plan(receipt, candidate), release.override(previous, proxy))


class ImageStoreCompatibilityTests(unittest.TestCase):
    def archive(self, path, config=None, entries=1):
        config = config or {'os': 'linux', 'architecture': 'amd64',
                            'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + 'd' * 64]}}
        data = json.dumps(config).encode()
        digest = 'sha256:' + hashlib.sha256(data).hexdigest()
        with tarfile.open(path, 'w') as archive:
            for name, body in [('manifest.json', json.dumps([{'Config': 'config.json'}] * entries).encode()),
                               ('config.json', data)]:
                member = tarfile.TarInfo(name)
                member.size = len(body)
                archive.addfile(member, io.BytesIO(body))
        return digest

    def exercise(self, host_same=False, changed=False, bad_output=False):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            config_id = self.archive(folder / 'app-image.tar')
            host_id = config_id if host_same else 'sha256:' + 'f' * 64
            info = dict(Id=host_id)
            def command(*args):
                if args[1] == 'load':
                    return 'Loaded image ID: ' + host_id if not bad_output else 'Loaded image: unexpected:tag'
                self.assertEqual(args[1], 'save')
                self.assertEqual(args[-1], host_id)
                self.archive(Path(args[3]), {'rootfs': {'diff_ids': []}} if changed else None)
                return ''
            with patch.object(release, 'command', side_effect=command), \
                    patch.object(release, 'inspect', return_value=info):
                return release.load_verified_image(folder, dict(image_id=config_id))

    def test_classic_same_id_and_containerd_different_id_preserve_config(self):
        for same in (True, False):
            info, config_id = self.exercise(host_same=same)
            self.assertTrue(info['Id'].startswith('sha256:'))
            self.assertEqual(info['Id'] == config_id, same)

    def test_changed_host_rootfs_config_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Host image configuration differs'):
            self.exercise(changed=True)

    def test_unexpected_load_response_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'exactly one loaded image ID'):
            self.exercise(bad_output=True)

    def test_archive_config_mismatch_stops_before_docker(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            self.archive(folder / 'app-image.tar')
            with patch.object(release, 'command') as command:
                with self.assertRaisesRegex(ValueError, 'Archive configuration identity differs'):
                    release.load_verified_image(folder, dict(image_id='sha256:' + '0' * 64))
                command.assert_not_called()

    def test_multiple_images_refused(self):
        with tempfile.TemporaryDirectory() as name:
            archive = Path(name) / 'image.tar'
            self.archive(archive, entries=2)
            with self.assertRaisesRegex(ValueError, 'exactly one image'):
                release.archive_config(archive)

    def test_schema_two_separates_source_image_and_config(self):
        manifest = dict(schema=2, source_commit='a' * 40, image_id='sha256:' + 'b' * 64,
                        config_id='sha256:' + 'c' * 64, archive_sha256='e' * 64,
                        implementation={n: 'd' * 64 for n in release.FILES})
        self.assertEqual(release.validate(manifest), manifest)
        del manifest['config_id']
        with self.assertRaisesRegex(ValueError, 'Invalid configuration identity'):
            release.validate(manifest)


if __name__ == '__main__':
    unittest.main()
