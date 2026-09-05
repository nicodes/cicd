#!/usr/bin/env python3
"""Build and test the exact patched Caddy source in disposable local storage."""
from pathlib import Path
import shutil
import subprocess
import tempfile

source = Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix='caddy-engineering-') as directory:
    root = Path(directory)
    for original, target in [('caddy.go.mod', 'go.mod'), ('caddy.go.sum', 'go.sum'), ('caddy-main.go', 'main.go')]:
        shutil.copyfile(source/original, root/target)
    subprocess.run(['sh', str(source/'caddy-build.sh'), '--test'], cwd=root, check=True, timeout=900)
