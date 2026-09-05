#!/usr/bin/env python3
"""Own one checkout's development stack; never discover shutdown targets by port."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import secrets
import select
import signal
import socket
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['run', 'stop'])
    parser.add_argument('--state', default='.local/dev')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    root = Path.cwd()
    state = root/args.state
    if state.is_symlink():
        raise RuntimeError('development state must not be a symlink')
    os.umask(0o077)
    state.mkdir(parents=True, exist_ok=True)
    if state.stat().st_uid != os.getuid() or state.stat().st_mode & 0o077:
        raise RuntimeError('development state must be owned by this user and mode 0700')
    os.chdir(state)  # Relative Unix socket names also work in long checkout paths.
    if args.operation == 'stop':
        try:
            token = Path('token').read_text().strip()
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(15)
                client.connect('control.sock')
                client.sendall(json.dumps({'stop': token}).encode()+b'\n')
                if client.recv(100) != b'stopped\n':
                    raise RuntimeError('development supervisor refused the stop request')
        except (FileNotFoundError, ConnectionRefusedError):
            print('No development stack owned by this checkout is running.')
        return
    command = args.command
    if command[:1] == ['--']:
        command = command[1:]
    if not command:
        parser.error('run requires a command after --')
    with Path('lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('this checkout already owns a development stack') from None
        Path('control.sock').unlink(missing_ok=True)
        token = secrets.token_hex(32)
        Path('token').write_text(token)
        stopping = False

        def stop_requested(_signum, _frame):
            nonlocal stopping
            stopping = True

        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, stop_requested)
        with socket.socket(socket.AF_UNIX) as server:
            server.bind('control.sock')
            server.listen(4)
            child = subprocess.Popen(command, cwd=root, start_new_session=True)
            clients = []
            try:
                while child.poll() is None and not stopping:
                    if not select.select([server], [], [], 0.1)[0]:
                        continue
                    client, _ = server.accept()
                    client.settimeout(2)
                    try:
                        request = client.recv(1024)
                        if json.loads(request).get('stop') == token:
                            stopping = True
                            clients.append(client)
                        else:
                            client.sendall(b'refused\n')
                            client.close()
                    except (ValueError, TimeoutError, OSError):
                        client.close()
            finally:
                # A live child remains our unreaped process-group leader, so a
                # stale PID file cannot cause this to signal somebody else's stack.
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait()
                for client in clients:
                    try:
                        client.sendall(b'stopped\n')
                    except OSError:
                        pass
                    client.close()
                Path('control.sock').unlink(missing_ok=True)
                Path('token').unlink(missing_ok=True)
            if not stopping and child.returncode:
                raise SystemExit(child.returncode if child.returncode > 0 else 128-child.returncode)


if __name__ == '__main__':
    main()
