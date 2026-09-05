#!/usr/bin/env python3
"""Run the online Bun audit; accept only independently verified local repairs."""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def verify_repair(app, repair):
    if datetime.date.fromisoformat(repair['review_date']) < datetime.date.today():
        raise RuntimeError('local security repair is overdue for upstream review')
    if not repair['owner'] or not repair['rationale']:
        raise RuntimeError('local security repair requires ownership and rationale')
    package = app/'node_modules'/repair['package']
    if json.loads((package/'package.json').read_text())['version'] != repair['version']:
        raise RuntimeError('security repair version does not match the installed package')
    for relative, expected in repair['installed_sha256'].items():
        target = package/relative
        if not target.resolve().is_relative_to(package.resolve()):
            raise RuntimeError('security repair names a file outside its package')
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'security repair is absent or modified: {relative}')
    patch = app/repair['patch']
    if not patch.resolve().is_relative_to(app.resolve()):
        raise RuntimeError('patch is outside the app')
    if hashlib.sha256(patch.read_bytes()).hexdigest() != repair['patch_sha256']:
        raise RuntimeError('reviewed dependency patch changed')
    # Refuse a second unpatched copy hidden beneath another dependency.
    for manifest in (app/'node_modules').rglob('image-size/package.json'):
        for relative, expected in repair['installed_sha256'].items():
            if hashlib.sha256((manifest.parent/relative).read_bytes()).hexdigest() != expected:
                raise RuntimeError('a nested image-size copy lacks the verified repair')


def main():
    app = Path(sys.argv[1] if len(sys.argv)>1 else 'app').resolve()
    result = subprocess.run(['bun', 'audit', '--json'], cwd=app,
                            text=True, capture_output=True, timeout=180)
    if result.returncode not in (0, 1):
        raise RuntimeError('online dependency audit could not complete')
    report = json.loads(result.stdout)
    if not isinstance(report, dict):
        raise RuntimeError('unrecognized audit response')
    if result.returncode and not report:
        raise RuntimeError('audit failed without findings; refusing a false pass')
    repairs_file = app/'security-remediations.json'
    repairs = json.loads(repairs_file.read_text()) if repairs_file.exists() else []
    repaired = {}
    for repair in repairs:
        if repair['package'] != 'image-size' or repair['version'] != '1.2.1':
            raise RuntimeError('unknown repair; add an independently reviewed verification first')
        if set(repair['advisories']) != {'GHSA-w3rx-r6r6-pgpr', 'GHSA-5p2g-fcmc-qvqq'}:
            raise RuntimeError('the image parser repair does not cover these advisories')
        verify_repair(app, repair)
        subprocess.run(['node', str(Path(__file__).with_name('image-parser-test.cjs')), str(app)], check=True, timeout=30)
        repaired[repair['package']] = set(repair['advisories'])
    remaining = []
    for package, findings in report.items():
        for finding in findings:
            advisory = finding['url'].rsplit('/', 1)[-1]
            if advisory not in repaired.get(package, set()):
                remaining.append(f'{package}: {finding["severity"]}: {finding["url"]}')
            else:
                print(f'Locally repaired and verified: {package}: {advisory}')
    if remaining:
        raise RuntimeError('unresolved dependency findings:\n'+'\n'.join(remaining))
    print('Online audit passed with all reported findings resolved or verified as locally repaired.')


if __name__ == '__main__':
    main()
