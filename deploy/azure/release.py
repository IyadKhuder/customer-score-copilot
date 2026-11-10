"""Owner-run release packaging and Linux activation; no Azure provisioning or secrets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

FILES = ('batch_scan.py', 'policy_decisions.py', 'temporal_policy.py',
         'case_application.py', 'review_workflow.py', 'review_graph.py')
IMAGE = re.compile(r'^sha256:[a-f0-9]{64}$')


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def checksum(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def inspect(reference):
    return json.loads(command('docker', 'image', 'inspect', reference))[0]


def fingerprints(reference):
    code = ('import hashlib,json; from pathlib import Path; '
            f'print(json.dumps({{n:hashlib.sha256((Path("/app/src")/n).read_bytes()).hexdigest() for n in {FILES!r}}}))')
    return json.loads(command('docker', 'run', '--rm', '--network', 'none',
                              '--entrypoint', 'python', reference, '-c', code))


def write_new(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def pack(image, output):
    root = Path(__file__).resolve().parents[2]
    # Refuse uncommitted tracked source; unrelated ignored/untracked diagnostics stay local.
    if command('git', '-C', str(root), 'status', '--porcelain', '--untracked-files=no'):
        raise ValueError('Commit tracked changes before packaging a release')
    if command('git', '-C', str(root), 'ls-files', '--others', '--exclude-standard',
               'src', 'frontend', 'policy_memos', 'data'):
        raise ValueError('Untracked application inputs must be reviewed before packaging')
    commit = command('git', '-C', str(root), 'rev-parse', 'HEAD')
    info = inspect(image)
    if (info.get('Config', {}).get('Labels') or {}).get('org.opencontainers.image.revision') != commit:
        raise ValueError('Build with the current Git commit revision label')
    if info['Architecture'] != 'amd64' or info['Os'] != 'linux':
        raise ValueError('Expected linux/amd64 image')
    hashes = fingerprints(info['Id'])
    expected = {n: checksum(root / 'src' / n) for n in FILES}
    if hashes != expected:
        raise ValueError('Image implementation differs from committed source')
    output.mkdir(parents=True, exist_ok=False)
    archive = output / 'app-image.tar'
    command('docker', 'save', '--output', str(archive), info['Id'])
    write_new(output / 'release.json', dict(schema=1, source_commit=commit,
              image_id=info['Id'], archive_sha256=checksum(archive), implementation=hashes))
    print('Release exported:', commit, info['Id'])


def validate(value):
    if value.get('schema') != 1 or not IMAGE.fullmatch(value.get('image_id', '')):
        raise ValueError('Invalid release identity')
    if not re.fullmatch(r'[a-f0-9]{40}', value.get('source_commit', '')):
        raise ValueError('Invalid source commit')
    if not re.fullmatch(r'[a-f0-9]{64}', value.get('archive_sha256', '')):
        raise ValueError('Invalid archive checksum')
    hashes = value.get('implementation', {})
    if set(hashes) != set(FILES) or any(not re.fullmatch(r'[a-f0-9]{64}', v) for v in hashes.values()):
        raise ValueError('Invalid implementation fingerprints')
    return value


def compose(*args):
    return command('docker', 'compose', '-f', 'compose.yaml', '-f', 'release.override.json', *args)


def active(service, require_running=True):
    ids = command('docker', 'compose', '-f', 'compose.yaml', 'ps', '--all', '-q', service).splitlines()
    if len(ids) != 1:
        raise ValueError('Start and verify the existing services before release')
    state = json.loads(command('docker', 'inspect', ids[0]))[0]
    if require_running and not state['State']['Running']:
        raise ValueError('Existing service is not running')
    return state


def override(api, proxy):
    if not IMAGE.fullmatch(api) or not IMAGE.fullmatch(proxy):
        raise ValueError('Expected immutable local image IDs')
    return {'services': {'api': {'image': api, 'pull_policy': 'never'},
                         'proxy': {'image': proxy, 'pull_policy': 'never'}}}


def ready():
    import urllib.request
    for _ in range(30):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2) as response:
                body = json.load(response)
            if body == {'status': 'ready', 'mode': 'offline', 'live_notes': False}:
                return
        except (OSError, ValueError):
            pass
        time.sleep(2)
    raise RuntimeError('Readiness failed; inspect and explicitly roll back')


def deployment_plan(directory, previous_api, previous_proxy):
    manifest = validate(json.loads((directory / 'release.json').read_text()))
    if (directory / 'activation.json').exists():
        raise ValueError('Activation receipt exists; inspect it before another deployment')
    if checksum(directory / 'app-image.tar') != manifest['archive_sha256']:
        raise ValueError('Archive checksum mismatch')
    command('docker', 'load', '--input', str(directory / 'app-image.tar'))
    info = inspect(manifest['image_id'])
    if info['Architecture'] != 'amd64' or info['Os'] != 'linux':
        raise ValueError('Expected linux/amd64 image')
    if (info.get('Config', {}).get('Labels') or {}).get('org.opencontainers.image.revision') != manifest['source_commit']:
        raise ValueError('Loaded image source revision differs from manifest')
    if fingerprints(info['Id']) != manifest['implementation']:
        raise ValueError('Loaded image fingerprints differ from manifest')
    if fingerprints(previous_api) != manifest['implementation']:
        raise ValueError('Implementation changed; explicit compatibility work required')
    return dict(source_commit=manifest['source_commit'],
                previous=override(previous_api, previous_proxy),
                candidate=override(info['Id'], previous_proxy))


def rollback_plan(receipt, current):
    target = override(receipt['previous']['services']['api']['image'],
                      receipt['previous']['services']['proxy']['image'])
    if current != receipt['candidate']['services']['api']['image']:
        raise ValueError('Running image differs from this release; inspect before rollback')
    if fingerprints(target['services']['api']['image']) != fingerprints(current):
        raise ValueError('Implementation compatibility changed; do not downgrade automatically')
    # Both retained images must still exist. Never pull or rebuild during rollback.
    inspect(target['services']['proxy']['image'])
    return target


def activate(directory, rollback=False):
    if os.name != 'posix' or os.geteuid() != 0 or Path.cwd() != Path('/opt/scorecopilot'):
        raise ValueError('Run as root from /opt/scorecopilot on the existing VM')
    # Serialize release operations; this does not replace the application database lease.
    import fcntl
    with Path('/opt/scorecopilot/release.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt_path = directory / 'activation.json'
        if rollback:
            receipt = json.loads(receipt_path.read_text())
            target = rollback_plan(receipt, active('api', require_running=False)['Image'])
        else:
            previous_api, previous_proxy = active('api'), active('proxy')
            ready()
            receipt = deployment_plan(directory, previous_api['Image'], previous_proxy['Image'])
            target = receipt['candidate']
            write_new(receipt_path, receipt)
        # JSON is accepted as a Compose file. No env-file or state changes.
        temporary = Path('release.override.json.tmp')
        temporary.write_text(json.dumps(target, indent=2) + '\n')
        temporary.replace('release.override.json')
        compose('up', '--detach', '--wait')
        ready()
        print('Rollback ready' if rollback else 'Release ready', target['services']['api']['image'])
        print('Still required: trusted public HTTPS and original saved-case readback.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    build = sub.add_parser('pack')
    build.add_argument('image')
    build.add_argument('directory', type=Path)
    for name in ('deploy', 'rollback'):
        sub.add_parser(name).add_argument('directory', type=Path)
    args = parser.parse_args()
    if args.action == 'pack':
        pack(args.image, args.directory)
    else:
        activate(args.directory.resolve(), args.action == 'rollback')
