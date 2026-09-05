#!/usr/bin/env python3
"""Exercise encryption and a complete offline restore using disposable local data."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import tarfile
import tempfile


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(project, release):
    restore = load('restore-drill')
    snapshot = load('snapshot')
    revision = json.loads(release.read_text())['revision']
    component = 'service' if project == 'komizo' else 'db'
    reference = f'ghcr.io/nicodes/{project}-{component}:{revision}'
    image = json.loads(restore.docker('image', 'inspect', reference))[0]['Id']
    binary = {'ormos': '/app/pocketbase', 'cazper': '/app/cazper-pocketbase', 'komizo': '/app/komizo-service'}[project]
    # Empty configuration is intentional: this fixture cannot inspect any live
    # container or obtain a real settings encryption key.
    os.environ['PB_ENCRYPTION_KEY'] = ''
    with tempfile.TemporaryDirectory(prefix=f'{project}-restore-fixture-') as directory:
        root = Path(directory)
        data = root/'data'
        data.mkdir(mode=0o700)
        uid = os.getuid() or 65534
        if os.getuid() == 0:
            os.chown(data, uid, uid)
        name = project+'-restore-fixture-'+secrets.token_hex(8)
        try:
            restore.docker('run', '--name', name, '--network=none', '--read-only', '--user', f'{uid}:{uid}',
                '--cap-drop=ALL', '--security-opt=no-new-privileges:true', '--memory=192m', '--cpus=1', '--pids-limit=128',
                '--tmpfs', '/tmp:rw,noexec,nosuid,size=32m', '--mount', f'type=bind,src={data},dst=/app/pb_data',
                '--entrypoint', binary, image, 'superuser', 'upsert', 'fixture@verification.invalid',
                secrets.token_hex(32), '--dir=/app/pb_data', timeout=180)
        finally:
            restore.docker('rm', '-f', '-v', name)
        archive = root/'data.tar.gz'
        with tarfile.open(archive, 'w:gz') as bundle:
            for item in sorted(data.rglob('*')):
                bundle.add(item, arcname=str(item.relative_to(data)), recursive=False)
        (root/'verification.json').write_text(json.dumps({
            'archive_sha256': snapshot.sha256(archive), 'image_id': image, 'image_reference': reference,
            'verified_at': datetime.now(timezone.utc).isoformat()}))
        key, cert = root/'key.pem', root/'recipient.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-noenc', '-keyout', str(key),
            '-out', str(cert), '-days', '1', '-subj', '/CN=disposable-restore-fixture'], check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        sealed = snapshot.seal(root, cert)
        export = root/'export.tar'
        with tarfile.open(export, 'w') as bundle:
            for file in ('snapshot.cms', 'receipt.json'):
                bundle.add(sealed/file, arcname=file)
        # Exercise the same bounded stdin path as the fixed host wrapper.
        with export.open('rb') as incoming:
            result = subprocess.run(['python3', str(Path(__file__).with_name('restore-drill.py')),
                '--project', project, '--export', '-', '--key', str(key)], stdin=incoming,
                text=True, capture_output=True, timeout=930)
        if result.returncode:
            raise RuntimeError('offline restore fixture failed: '+result.stderr)
        report = json.loads(result.stdout)
        assert report['cleanup'] == 'passed' and report['published_ports'] == []
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', choices=['ormos', 'cazper', 'komizo'], required=True)
    parser.add_argument('--release', type=Path, default=Path('.artifacts/release/release.json'))
    args = parser.parse_args()
    os.umask(0o077)
    main(args.project, args.release)
