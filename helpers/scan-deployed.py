#!/usr/bin/env python3
"""Scan the latest attempted and last successful production revisions."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess


PRODUCTS = {
    'ormos': ('nicodes', ['api', 'db', 'gate']),
    'cazper': ('nicodes', ['api', 'db', 'gate']),
    'komizo': ('nicodes', ['service', 'gate']),
    'gdam': ('aviorstudio', ['api', 'db', 'gate']),
    'termcade': ('aviorstudio', ['api', 'db', 'gate', 'maintenance']),
    'astry': ('astrylogical', ['api', 'pb', 'gate']),
}

def product_images(project, repository, revision):
    owner, components = PRODUCTS[project]
    if repository != f'{owner}/{project}-be' or not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise ValueError('repository or revision does not match the declared product')
    return [f'ghcr.io/{owner}/{project}-{component}:{revision}' for component in components]


def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', path], text=True, timeout=30))


def deployed_revisions(repository):
    candidates = []
    for page in range(1, 11):
        deployments = api(f'repos/{repository}/deployments?environment=production&per_page=100&page={page}')
        if not isinstance(deployments, list):
            raise ValueError('invalid deployment list')
        for deployment in deployments:
            identity = deployment.get('id')
            if type(identity) is not int or identity <= 0:
                raise ValueError('invalid deployment identity')
            statuses = api(f'repos/{repository}/deployments/{identity}/statuses?per_page=100')
            if not isinstance(statuses, list):
                raise ValueError('invalid deployment status list')
            # Use the latest status. An older success followed by failure must
            # never be interpreted as the current successful deployment.
            if statuses:
                state = statuses[0].get('state')
                if state not in {'success', 'failure', 'error', 'pending', 'queued', 'in_progress', 'inactive'}:
                    raise ValueError('unknown deployment state')
                revision = deployment.get('sha', '')
                if not re.fullmatch(r'[a-f0-9]{40}', revision):
                    raise ValueError('deployment has no exact commit identity')
                # A failed deploy may have already replaced some containers.
                # Cover that attempt as well as the previous working release.
                if not candidates:
                    candidates.append(revision)
                if state == 'success':
                    return list(dict.fromkeys([*candidates, revision]))
        if len(deployments) < 100:
            break
    raise ValueError('no successful production deployment found within the lookup bound')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, choices=list(PRODUCTS))
    args = parser.parse_args()
    owner, components = PRODUCTS[args.project]
    repository = os.environ.get('GITHUB_REPOSITORY', f'{owner}/{args.project}-be')
    if repository != f'{owner}/{args.project}-be':
        raise ValueError('repository does not match the declared product')
    revisions = deployed_revisions(repository)
    spec = importlib.util.spec_from_file_location('scan_image', Path(__file__).with_name('scan-image.py'))
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    results, failures = [], []
    for revision in revisions:
        for image in product_images(args.project, repository, revision):
            try:
                subprocess.run(['docker', 'pull', image], check=True, timeout=300)
                results.append(scanner.scan(image))
            except (ValueError, subprocess.SubprocessError) as error:
                failures.append(f'{image}: {error}')
    print(json.dumps({'revisions': revisions, 'images': results, 'failures': failures}, indent=2))
    if failures:
        raise SystemExit(1)
