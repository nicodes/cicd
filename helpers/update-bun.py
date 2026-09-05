#!/usr/bin/env python3
"""Use the repository's Bun for update inventory and an owner-reviewed refresh PR."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def run(args, **kwargs):
    result = subprocess.run(args, text=True, capture_output=True, timeout=kwargs.pop('timeout', 60), **kwargs)
    if result.returncode:
        raise RuntimeError(f'{args[0]} {args[1]} failed (exit {result.returncode})')
    return result.stdout


def api(repo, suffix, payload=None):
    args = ['gh', 'api', f'repos/{repo}/{suffix}']
    if payload is not None:
        args += ['--method', 'POST', '--input', '-']
    result = run(args, input=None if payload is None else json.dumps(payload))
    return json.loads(result) if result.strip() else None


def parse_inventory(output):
    """Pinned Bun emits a table, not JSON. Reject unknown rows/schema."""
    rows = []
    for line in output.splitlines():
        line = line.strip()
        if not line or re.fullmatch(r'bun outdated v\d+\.\d+\.\d+ \([a-f0-9]+\)', line):
            continue
        if re.fullmatch(r'\|[-|]+\|', line):
            continue
        cells = [cell.strip() for cell in line.split('|')[1:-1]]
        if cells == ['Package', 'Current', 'Update', 'Latest']:
            continue
        if not line.startswith('|') or not line.endswith('|') or len(cells) != 4:
            raise ValueError('Bun outdated output schema changed')
        name = re.sub(r' \((dev|optional|peer)\)$', '', cells[0])
        if not re.fullmatch(r'(?:@[a-z0-9._-]+/)?[a-z0-9._-]+', name):
            raise ValueError('Unknown package name in Bun inventory')
        if not all(re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.+-]+)?', value) for value in cells[1:]):
            raise ValueError('Unknown version in Bun inventory')
        rows.append(dict(zip(['package', 'current', 'update', 'latest'], [name, *cells[1:]])))
    return rows


def prepare(root):
    manifest = json.loads((root/'app/package.json').read_text())
    version = run(['bun', '--version']).strip()
    if manifest.get('packageManager') != 'bun@'+version:
        raise ValueError('Update automation must use the exact repository Bun pin')
    tracked = set(run(['git', 'ls-files', '-z'], cwd=root).split('\0'))
    required = ['app/package.json', 'app/bun.lock']
    required += ['app/'+value for value in manifest.get('patchedDependencies', {}).values()]
    if 'app/bunfig.toml' in tracked:
        required.append('app/bunfig.toml')
    # Dependency resolution gets no GitHub credential and executes no lifecycle scripts.
    env = {name: value for name, value in os.environ.items()
           if name not in {'GH_TOKEN', 'GITHUB_TOKEN', 'NODE_AUTH_TOKEN', 'NPM_TOKEN'}}
    env['NO_COLOR'] = '1'
    with tempfile.TemporaryDirectory(prefix='bun-update-') as temporary:
        work = Path(temporary)
        for name in required:
            source = root/name
            if name not in tracked or source.is_symlink() or not source.resolve().is_relative_to(root.resolve()/'app'):
                raise ValueError('Bun update input must be a tracked app file')
            target = work/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        inventory = parse_inventory(run(['bun', 'outdated'], cwd=work/'app', env=env, timeout=600))
        run(['bun', 'update', '--lockfile-only', '--ignore-scripts', '--no-progress'],
            cwd=work/'app', env=env, timeout=900)
        updated = json.loads((work/'app/package.json').read_text())
        # Preserve policies, overrides and patch declarations. Only Bun's dependency
        # range maintenance may change the manifest; no scripts or tooling edits.
        groups = {'dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies'}
        if {k:v for k,v in manifest.items() if k not in groups} != {k:v for k,v in updated.items() if k not in groups}:
            raise ValueError('Bun changed non-dependency manifest fields')
        changes = {name: (work/name).read_text() for name in ['app/package.json', 'app/bun.lock']
                   if (work/name).read_bytes() != (root/name).read_bytes()}
    return inventory, changes


def publish(root, repo, base, inventory, changes):
    if not re.fullmatch(r'nicodes/[a-z0-9-]+', repo) or not re.fullmatch(r'[a-f0-9]{40}', base):
        raise ValueError('Invalid publication target')
    if not set(changes).issubset({'app/package.json', 'app/bun.lock'}):
        raise ValueError('Only Bun manifest and lockfile changes may be published')
    if api(repo, 'git/ref/heads/main')['object']['sha'] != base:
        raise ValueError('Main changed; rerun the update workflow on current main')
    title = 'Bun maintenance: available dependency updates'
    existing = [i for i in api(repo, 'issues?state=open&per_page=100') if i['title'] == title and 'pull_request' not in i]
    if inventory:
        rows = ''.join(f"| `{i['package']}` | `{i['current']}` | `{i['update']}` | `{i['latest']}` |\n" for i in inventory)
        body = ('Owner: @nicodes\n\nThe pinned Bun found updates. `Update` respects the current manifest range; '
                '`Latest` also shows releases outside that range. Review those releases separately.\n\n'
                '| Package | Current | Update | Latest |\n|---|---|---|---|\n'+rows+
                '\nWithin-range and transitive refreshes are proposed in an owner-reviewed PR. '
                'Authentication/cryptography, PocketBase, pre-1.0 minor and major updates always require review. '
                'Every resulting PR must pass the complete Test and Build gates.\n')
        if len(body) > 50000:
            raise ValueError('Update inventory exceeds the issue size limit')
        if existing:
            run(['gh', 'api', f'repos/{repo}/issues/{min(i["number"] for i in existing)}', '--method', 'PATCH', '--input', '-'],
                input=json.dumps({'body': body, 'assignees': ['nicodes']}))
        else:
            api(repo, 'issues', {'title': title, 'body': body, 'assignees': ['nicodes']})
    if not changes:
        print('No within-range lockfile or manifest changes; inventory recorded.')
        return
    pending = api(repo, 'pulls?state=open&base=main&per_page=100')
    for pr in pending:
        if not pr['head']['ref'].startswith('automation/bun-refresh-'):
            continue
        if pr['head'].get('repo', {}).get('full_name') != repo:
            continue
        checks = api(repo, f'commits/{pr["head"]["sha"]}/check-runs?per_page=100')
        if not {'Test', 'Build'}.issubset({check['name'] for check in checks['check_runs']}):
            api(repo, 'actions/workflows/ci.yml/dispatches', {'ref': pr['head']['ref']})
        print('An owner-reviewed Bun refresh PR is already open; its branch is preserved.')
        return
    digest = hashlib.sha256(json.dumps(changes, sort_keys=True).encode()).hexdigest()[:12]
    branch = f'automation/bun-refresh-{base[:12]}-{digest}'
    commit = api(repo, 'git/commits/'+base)
    tree = api(repo, 'git/trees', {'base_tree': commit['tree']['sha'], 'tree': [
        {'path': name, 'mode': '100644', 'type': 'blob', 'content': content} for name,content in changes.items()]})
    refs = api(repo, 'git/matching-refs/heads/'+branch)
    exact = [ref for ref in refs if ref['ref'] == 'refs/heads/'+branch]
    if exact:
        prior = api(repo, 'git/commits/'+exact[0]['object']['sha'])
        if prior['tree']['sha'] != tree['sha'] or [p['sha'] for p in prior['parents']] != [base]:
            raise ValueError('Existing automation branch changed; owner review required')
    else:
        new = api(repo, 'git/commits', {'message': 'Refresh Bun dependencies within declared ranges', 'tree': tree['sha'], 'parents': [base]})
        api(repo, 'git/refs', {'ref': 'refs/heads/'+branch, 'sha': new['sha']})
    pr = api(repo, 'pulls', {'title': 'Refresh Bun dependencies within declared ranges', 'head': branch, 'base': 'main',
        'body': 'The repository-pinned Bun refreshed `app/package.json` and `app/bun.lock` with lifecycle scripts disabled. '
                'This grouped refresh requires owner review, including sensitive and pre-1.0 updates. '
                'It is never eligible for the Dependabot auto-merge path. Complete Test and Build checks are mandatory. '
                'Updates beyond declared ranges are listed in the owned Bun maintenance issue.'})
    api(repo, f'issues/{pr["number"]}/assignees', {'assignees': ['nicodes']})
    # GITHUB_TOKEN-created commits do not trigger push/PR workflows. Explicit
    # dispatch runs the same full CI on the generated branch, without production secrets.
    api(repo, 'actions/workflows/ci.yml/dispatches', {'ref': branch})
    print(f'Created owner-reviewed PR #{pr["number"]} and dispatched complete CI.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='resolve and report locally without GitHub writes')
    args = parser.parse_args()
    root = Path.cwd()
    inventory, changes = prepare(root)
    output = root/'.artifacts/bun-updates.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'inventory': inventory, 'changed_files': sorted(changes)}, indent=2)+'\n')
    if not args.dry_run:
        if os.environ.get('GITHUB_REF') != 'refs/heads/main':
            raise ValueError('Bun update publication is restricted to main')
        publish(root, os.environ['GITHUB_REPOSITORY'], run(['git', 'rev-parse', 'HEAD']).strip(), inventory, changes)
    print(f'Bun inventory: {len(inventory)} available updates; {len(changes)} proposed files.')


if __name__ == '__main__':
    main()
