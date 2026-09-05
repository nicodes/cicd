#!/usr/bin/env python3
"""Merge a routine Dependabot update only after every exact-head gate succeeds."""
import json
import os
import re
import subprocess
import time


def api(path, method='GET', body=None):
    command = ['gh', 'api', '--method', method, '-H', 'Accept: application/vnd.github+json',
               '-H', 'X-GitHub-Api-Version: 2022-11-28', path]
    if body is not None:
        command += ['--input', '-']
    result = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                            text=True, capture_output=True, timeout=60, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def pages(path, key=None):
    # check-runs and statuses both cap pages at 100. Never treat page one as all evidence.
    values = []
    for page in range(1, 101):
        data = api(f'{path}{"&" if "?" in path else "?"}per_page=100&page={page}')
        items = data[key] if key else data
        if not isinstance(items, list):
            raise ValueError('invalid paginated check response')
        values.extend(items)
        if len(items) < 100:
            return values
    raise ValueError('check pagination limit exceeded; require manual review')


def decision(checks, statuses, head, self_prefix, required=('Test', 'Build')):
    relevant = []
    for check in checks:
        if check.get('head_sha') != head:
            raise ValueError('check evidence belongs to another commit')
        if not check.get('details_url', '').startswith(self_prefix):
            relevant.append(check)
    seen = {c['name'] for c in relevant if c.get('app', {}).get('slug') == 'github-actions'
            and c.get('status') == 'completed' and c.get('conclusion') == 'success'}
    for check in relevant:
        if check.get('status') == 'completed' and check.get('conclusion') not in ('success', 'skipped'):
            raise ValueError(f'failed check: {check["name"]}')
        if check.get('name') in required and check.get('conclusion') == 'skipped':
            raise ValueError(f'required check was skipped: {check["name"]}')
    latest = {}
    for status in statuses:  # REST returns newest first.
        latest.setdefault(status['context'], status)
    if any(status.get('state') not in ('success', 'pending') for status in latest.values()):
        raise ValueError('a commit status failed')
    return (set(required) <= seen and all(c.get('status') == 'completed' for c in relevant)
            and all(s.get('state') == 'success' for s in latest.values()))


def main():
    repo, pr, head, run = [os.environ[key] for key in ['GITHUB_REPOSITORY', 'PR_NUMBER', 'EXPECTED_HEAD', 'GITHUB_RUN_ID']]
    if not re.fullmatch(r'(?:nicodes/[a-z0-9-]+|aviorstudio/(?:gdam|termcade)-be|astrylogical/astry-be)', repo) or not pr.isdigit() or not run.isdigit() or not re.fullmatch(r'[a-f0-9]{40}', head):
        raise ValueError('invalid merge identity')
    prefix = f'https://github.com/{repo}/actions/runs/{run}/'
    def pull():
        value = api(f'repos/{repo}/pulls/{pr}')
        if value.get('state') != 'open' or value.get('draft') is not False or value['head']['sha'] != head:
            raise ValueError('pull request changed or is not ready')
        if value['user']['login'] != 'dependabot[bot]' or value['base']['ref'] != 'main':
            raise ValueError('not a Dependabot update targeting main')
        return value
    deadline = time.monotonic()+5400
    while time.monotonic() < deadline:
        pull()
        checks = pages(f'repos/{repo}/commits/{head}/check-runs?filter=latest', 'check_runs')
        statuses = pages(f'repos/{repo}/commits/{head}/statuses', None)
        if decision(checks, statuses, head, prefix):
            pull()  # Close movement between evidence collection and the atomic SHA merge.
            result = api(f'repos/{repo}/pulls/{pr}/merge', 'PUT', {'sha': head, 'merge_method': 'squash'})
            if result.get('merged') is not True or not re.fullmatch(r'[a-f0-9]{40}', result.get('sha', '')):
                raise ValueError('GitHub did not confirm the merge')
            merged = result['sha']
            if api(f'repos/{repo}/git/ref/heads/main')['object']['sha'] != merged:
                raise ValueError('main moved before workflow dispatch; review the newer main run')
            workflow = 'ci.yml' if repo.endswith('/petalboard-be') else 'cd.yml'
            # GITHUB_TOKEN merges suppress push events. Dispatch the full merged gate explicitly.
            api(f'repos/{repo}/actions/workflows/{workflow}/dispatches', 'POST', {'ref': 'main'})
            print(f'Merged checked head {head}; dispatched {workflow} for merged main {merged}')
            return
        print('Waiting for the complete Test and Build gate', flush=True)
        time.sleep(15)
    raise TimeoutError('full gate did not complete within 90 minutes; no merge performed')


if __name__ == '__main__':
    main()
