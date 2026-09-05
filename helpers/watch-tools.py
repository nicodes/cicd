#!/usr/bin/env python3
"""Watch exact repository tool pins and keep one owned, actionable update issue."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib


def updates(root, installed, outdated):
    config = (root / '.mise.toml').resolve()
    result = []
    for name, item in sorted(outdated.items()):
        # mise can return inherited tools even with --local. Never update them.
        if Path(item['source']['path']).resolve() != config:
            continue
        current = installed[name]
        latest = item.get('bump') or item.get('latest')
        if not isinstance(current, str) or not re.fullmatch(r'\d+\.\d+\.\d+', current):
            raise ValueError(f'{name}: expected an exact repository version')
        if not isinstance(latest, str) or not re.fullmatch(r'\d+\.\d+\.\d+', latest):
            raise ValueError(f'{name}: unrecognized upstream version')
        if tuple(map(int, latest.split('.'))) > tuple(map(int, current.split('.'))):
            result.append((name, current, latest))
    return result


def main():
    root = Path.cwd()
    installed = tomllib.loads((root / '.mise.toml').read_text())['tools']
    outdated = json.loads(subprocess.check_output(
        ['mise', 'outdated', '--bump', '--local', '--json'], text=True, timeout=600))
    pending = updates(root, installed, outdated)
    repo = os.environ['GITHUB_REPOSITORY']
    if not re.fullmatch(r'nicodes/[a-z0-9-]+', repo):
        raise ValueError('invalid repository')
    title = 'Tool maintenance: pinned versions have updates'
    issues = json.loads(subprocess.check_output(['gh', 'issue', 'list', '--repo', repo,
        '--state', 'open', '--search', f'in:title "{title}"', '--limit', '100',
        '--json', 'number,title'], text=True, timeout=60))
    issues = [item for item in issues if item['title'] == title]
    if not pending:
        print('All repository tool pins are current.')
        # Do not automatically close a human's maintenance investigation.
        return
    body = ('Owner: @nicodes\n\nThe scheduled tool watch found these upstream releases.\n\n'
            '| Tool | Pinned | Available |\n|---|---|---|\n' +
            ''.join(f'| `{name}` | `{old}` | `{new}` |\n' for name, old, new in pending) +
            '\nUpdate `.mise.toml` and every corresponding Go directive, Docker builder, '
            'download URL, and binary checksum together. Obtain checksums from the upstream '
            'release and verify the actual downloaded bytes. Keep the Bun packageManager '
            'field and lockfile in sync.\n\n'
            'Run **make check**, including online scans and browser journeys, on the resulting '
            'PR. PocketBase, authentication/cryptography, pre-1.0 minor and all major updates '
            'require owner review. This watcher does not authorize or auto-merge updates.\n')
    if len(body) > 15000:
        raise ValueError('too many tool updates for a bounded report')
    with tempfile.TemporaryDirectory() as directory:
        file = Path(directory) / 'body.md'
        file.write_text(body)
        command = (['gh', 'issue', 'edit', str(min(i['number'] for i in issues))] if issues
                   else ['gh', 'issue', 'create', '--title', title])
        subprocess.run([*command, '--repo', repo, '--assignee', 'nicodes', '--body-file', str(file)],
                       check=True, timeout=60)


if __name__ == '__main__':
    main()
