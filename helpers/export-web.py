#!/usr/bin/env python3
"""Export Expo with a bounded process lifetime and require a successful exit."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess


def export(app, output_name, timeout=900, command=None):
    app = Path(app).resolve(strict=True)
    if output_name not in ('dist', 'dist-e2e', 'dist-check'):
        raise ValueError('output must be dist, dist-e2e, or dist-check inside the app')
    output = app / output_name
    if output.is_symlink():
        raise ValueError('export output must not be a symlink')
    if output.exists():
        shutil.rmtree(output)
    process = subprocess.Popen(command or ['bun', 'x', '--no-install', 'expo', 'export',
        '--platform', 'web', '--output-dir', output_name, '--clear'], cwd=app,
        env={**os.environ, 'CI': '1'}, start_new_session=True)
    try:
        status = process.wait(timeout=timeout)
    finally:
        # The unreaped leader owns this process group throughout a timeout.
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    if status:
        raise RuntimeError(f'Expo exited {status}; artifacts do not override a failed export')
    if not (output/'index.html').is_file() or not (output/'index.html').stat().st_size:
        raise RuntimeError('Expo did not produce a nonempty index.html')
    files = sorted(p for p in output.rglob('*') if p.is_file())
    if not any(p.suffix == '.js' for p in files):
        raise RuntimeError('Expo did not produce JavaScript assets')
    if any(p.is_symlink() for p in output.rglob('*')):
        raise RuntimeError('export contains a symlink')
    manifest = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    (output/'export-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(f'Validated {len(files)} exported files in {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', default='app')
    parser.add_argument('--output', default='dist')
    parser.add_argument('--timeout', type=int, default=900)
    args = parser.parse_args()
    export(args.app, args.output, args.timeout)
