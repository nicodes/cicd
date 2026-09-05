#!/usr/bin/env python3
"""Freeze one database writer, archive its volume, and verify an isolated restore."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import gzip
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import sqlite3
import subprocess
import tarfile
import tempfile
import time

MAX_FILES = 100000
MAX_BYTES = 16 * 1024**3
AUXILIARY = {'auxiliary.db', 'auxiliary.db-wal', 'auxiliary.db-shm'}


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def restore(archive, target, expected=None):
    """Extract regular files only, bounding expansion and rejecting all links."""
    if archive.is_symlink() or not archive.is_file():
        raise ValueError('snapshot must be a regular archive')
    archive_hash = sha256(archive)
    if expected is not None and archive_hash != expected:
        raise ValueError('snapshot checksum mismatch')
    # tar can stop at its end marker without reading gzip's checksum/trailer.
    # Consume the complete compressed stream first so truncation cannot pass.
    with gzip.open(archive, 'rb') as compressed:
        total = 0
        while block := compressed.read(1024*1024):
            total += len(block)
            if total > MAX_BYTES + MAX_FILES * 4096:
                raise ValueError('compressed archive expansion exceeds its bound')
    if any(target.iterdir()):
        raise ValueError('restore target must be empty')
    files = {}
    seen = set()
    expanded = 0
    with tarfile.open(archive, 'r:gz') as source:
        for member in source:
            path = PurePosixPath(member.name)
            if (not member.name or path.is_absolute() or '..' in path.parts or
                    str(path) != member.name or member.name in seen or
                    not (member.isdir() or member.isfile())):
                raise ValueError(f'unsafe or duplicate archive member: {member.name!r}')
            seen.add(member.name)
            expanded += member.size
            if len(seen) > MAX_FILES or expanded > MAX_BYTES or member.size < 0:
                raise ValueError('snapshot expansion exceeds its bound')
            destination = target / member.name
            if member.isdir():
                destination.mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with source.extractfile(member) as incoming, destination.open('xb') as output:
                shutil.copyfileobj(incoming, output, length=1024*1024)
            destination.chmod(0o600)
            if destination.stat().st_size != member.size:
                raise ValueError('archive member was truncated')
            files[member.name] = sha256(destination)
    if 'data.db' not in files:
        raise ValueError('snapshot is missing PocketBase data.db')
    databases = {}
    for file in sorted(target.rglob('*.db')):
        # Open the COPY normally so SQLite reads/replays its WAL. immutable=1
        # would silently ignore committed data that still lives in that WAL.
        with closing(sqlite3.connect(file, timeout=10)) as database:
            database.execute('PRAGMA trusted_schema=OFF')
            result = database.execute('PRAGMA integrity_check').fetchall()
            if result != [('ok',)]:
                raise ValueError(f'SQLite integrity failure: {file.relative_to(target)}')
            tables = sorted(row[0] for row in database.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"))
            if file.name == 'data.db' and not {'_collections', '_migrations'} <= set(tables):
                raise ValueError('data.db is not the expected application database')
            databases[str(file.relative_to(target))] = {'integrity': 'ok', 'tables': len(tables)}
    return {'archive_sha256': archive_hash, 'expanded_bytes': expanded,
            'files': files, 'databases': databases}


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, timeout=30).strip()


def inspect(container):
    result = json.loads(docker('inspect', container))
    if len(result) != 1 or not re.fullmatch(r'[a-f0-9]{64}', result[0]['Id']):
        raise ValueError('database writer identity is ambiguous')
    return result[0]


def timestamp_ns(value):
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?Z', value)
    if not match:
        raise ValueError('Docker health timestamp is not recognized')
    seconds = int(datetime.strptime(match[1], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc).timestamp())
    return seconds * 10**9 + int((match[2] or '').ljust(9, '0'))


def resumed_health(container, boundary, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = inspect(container)
        state = info['State']
        health = state.get('Health', {})
        if not state['Running'] or state['Paused'] or health.get('Status') not in {'starting', 'healthy', 'unhealthy'}:
            raise ValueError('resumed writer has no usable healthcheck')
        for probe in health.get('Log', []):
            started = timestamp_ns(probe['Start'])
            if (health['Status'] == 'healthy' and probe['ExitCode'] == 0 and started > boundary
                    and time.monotonic() < deadline):
                return
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    raise TimeoutError('no successful health probe started after the writer resumed')


def snapshot(container, mount, destination, exclude_auxiliary=False):
    info = inspect(container)
    state = info['State']
    if not state['Running'] or state['Paused'] or state.get('Restarting'):
        raise ValueError('snapshot requires a running, unpaused database writer')
    if not info.get('Config', {}).get('Healthcheck', {}).get('Test'):
        raise ValueError('database writer must have a configured healthcheck')
    matches = [entry for entry in info['Mounts'] if entry['Destination'] == mount and entry['Type'] in {'volume', 'bind'}]
    if len(matches) != 1:
        raise ValueError('database data mount is missing or ambiguous')
    source = Path(matches[0]['Source']).resolve(strict=True)
    destination.mkdir(parents=True, mode=0o700, exist_ok=True)
    destination = destination.resolve(strict=True)
    if destination == source or source in destination.parents or destination.stat().st_mode & 0o077:
        raise ValueError('backup destination must be private and outside the data volume')
    excluded = AUXILIARY if exclude_auxiliary else set()
    candidates = [path for path in source.rglob('*') if str(path.relative_to(source)) not in excluded]
    if any(path.is_symlink() or not (path.is_file() or path.is_dir()) for path in candidates):
        raise ValueError('database volume contains links or special files')
    size = sum(path.stat().st_size for path in candidates if path.is_file())
    if len(candidates) > MAX_FILES or size > MAX_BYTES:
        raise ValueError('database snapshot exceeds its bound')
    if shutil.disk_usage(destination).free < 2 * size + 512 * 1024**2:
        raise ValueError('insufficient headroom for both the snapshot and its isolated restore')
    writer = info['Id']
    with tempfile.TemporaryDirectory(prefix='.pending-', dir=destination) as temporary:
        pending = Path(temporary)
        archive = pending / 'data.tar.gz'
        paused = False
        try:
            # A pause error is NEVER interpreted as evidence that a writer stopped.
            paused = True
            docker('pause', writer)
            if not inspect(writer)['State']['Paused']:
                raise ValueError('database writer did not pause')
            with tarfile.open(archive, 'w:gz', dereference=False) as output:
                count, copied = 0, 0
                for path in sorted(source.rglob('*')):
                    relative = str(path.relative_to(source))
                    if relative in excluded:
                        continue
                    metadata = output.gettarinfo(path, arcname=relative)
                    count += 1
                    copied += metadata.size
                    if count > MAX_FILES or copied > MAX_BYTES or copied > size + 256 * 1024**2:
                        raise ValueError('database grew beyond the reserved snapshot budget')
                    if not (metadata.isdir() or metadata.isfile()):
                        raise ValueError('database volume changed to contain a link or special file')
                    if metadata.isfile():
                        with path.open('rb') as stream:
                            output.addfile(metadata, stream)
                    else:
                        output.addfile(metadata)
        finally:
            if paused:
                docker('unpause', writer)
                boundary = time.time_ns()
                resumed_health(writer, boundary)
        restored = pending / 'restored'
        restored.mkdir(mode=0o700)
        evidence = restore(archive, restored)
        evidence.update({'writer_id': writer, 'image_id': info['Image'],
                         'excluded': sorted(excluded), 'verified_at': datetime.now(timezone.utc).isoformat()})
        name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + evidence['archive_sha256'][:12]
        final = destination / name
        final.mkdir(mode=0o700)
        archive.rename(final / 'data.tar.gz')
        (final / 'data.tar.gz').chmod(0o600)
        (final / 'verification.json').write_text(json.dumps(evidence, indent=2) + '\n')
        print(final)
        return final


def seal(directory, recipient):
    """Encrypt the verified snapshot to a public recovery certificate."""
    archive = directory / 'data.tar.gz'
    evidence = json.loads((directory / 'verification.json').read_text())
    if sha256(archive) != evidence['archive_sha256']:
        raise ValueError('snapshot changed after verification')
    output = directory / 'offhost'
    output.mkdir(mode=0o700)
    ciphertext = output / 'snapshot.cms'
    subprocess.run(['openssl', 'cms', '-encrypt', '-aes-256-gcm', '-binary', '-outform', 'DER',
                    '-in', str(archive), '-out', str(ciphertext), str(recipient)], check=True, timeout=600)
    receipt = {key: evidence[key] for key in ['archive_sha256', 'image_id', 'verified_at']}
    receipt['ciphertext_sha256'] = sha256(ciphertext)
    (output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return output


def unseal(directory, key, target):
    """Authenticate/decrypt an off-host copy, then validate its isolated restore."""
    receipt = json.loads((directory / 'receipt.json').read_text())
    ciphertext = directory / 'snapshot.cms'
    if sha256(ciphertext) != receipt['ciphertext_sha256']:
        raise ValueError('off-host ciphertext checksum mismatch')
    with tempfile.TemporaryDirectory(prefix='snapshot-decrypt-') as temporary:
        archive = Path(temporary) / 'data.tar.gz'
        subprocess.run(['openssl', 'cms', '-decrypt', '-binary', '-inform', 'DER',
                        '-in', str(ciphertext), '-inkey', str(key), '-out', str(archive)], check=True, timeout=600)
        return restore(archive, target, receipt['archive_sha256'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    create = commands.add_parser('create')
    create.add_argument('--container', required=True)
    create.add_argument('--mount', required=True)
    create.add_argument('--destination', type=Path, required=True)
    create.add_argument('--exclude-auxiliary', action='store_true')
    verify = commands.add_parser('verify')
    verify.add_argument('archive', type=Path)
    verify.add_argument('--sha256', required=True)
    encrypt = commands.add_parser('seal')
    encrypt.add_argument('directory', type=Path)
    encrypt.add_argument('--recipient', type=Path, required=True)
    decrypt = commands.add_parser('unseal')
    decrypt.add_argument('directory', type=Path)
    decrypt.add_argument('--key', type=Path, required=True)
    decrypt.add_argument('--target', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    def interrupted(number, _frame):
        raise InterruptedError(f'snapshot interrupted by signal {number}')
    for number in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(number, interrupted)
    if args.operation == 'create':
        snapshot(args.container, args.mount, args.destination, args.exclude_auxiliary)
    elif args.operation == 'seal':
        print(seal(args.directory, args.recipient))
    elif args.operation == 'unseal':
        print(json.dumps(unseal(args.directory, args.key, args.target), indent=2))
    else:
        with tempfile.TemporaryDirectory(prefix='snapshot-verify-') as temporary:
            print(json.dumps(restore(args.archive, Path(temporary), args.sha256), indent=2))


if __name__ == '__main__':
    main()
