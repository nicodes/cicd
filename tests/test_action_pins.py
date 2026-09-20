import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
PINS = Path(__file__).parents[1] / 'helpers/pins.mjs'
UPDATER = Path(__file__).parents[1] / 'helpers/action-pins.mjs'

# Authoritative komizo-actions tag data, resolved live with
# git ls-remote https://github.com/nicodes/komizo-actions 'refs/tags/*'.
V1_TAG, V1 = '29cdb643332704ce93749e4ebb3a29d1278a7566', 'ab3ebcd80a45ed86883c38a3c2a73bccaa4f211e'
V2_TAG, V2 = '84a7940697a1262328453c301080178d0fdb8aee', '58b5930cf3bbc3ad378cd90c817f81fc4616e5f3'
V3_TAG, V3 = 'cff4d4a8d32478f5bd70157baaa95e13308c39fe', 'dd77ee8778d82ce25ee9e8273debffd5b92e4f1c'
V8_TAG, V8 = 'a4771a4b10cee3c4209039af635671233630ee89', '1c698467831e1351854130af19b1a1f4e10f50bc'
V10_TAG, V10 = 'b455240ddd13769d461f338aee9d98ddd80b0469', '3969f9541f731a50bb6efce14d318a02d6ec5c99'
CHECKOUT = 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1'
MISE = 'jdx/mise-action@c2a87611a18de5b3828c5652fe268e992400cb5c'

LS_REMOTE = ''.join(f'{tag}\trefs/tags/{name}\n{commit}\trefs/tags/{name}^{{}}\n'
                    for name, tag, commit in [('v0.0.1', V1_TAG, V1), ('v0.0.2', V2_TAG, V2),
                                              ('v0.0.3', V3_TAG, V3), ('v0.0.8', V8_TAG, V8),
                                              ('v0.0.10', V10_TAG, V10)])


def write_product(root, steps=(), composite=None, record=None, applications=('app',)):
    """Build the smallest product tree that passes every other pins.mjs check."""
    engineering = root / 'scripts/engineering'
    (engineering / 'helpers').mkdir(parents=True)
    helper = engineering / 'helpers/pins.mjs'
    helper.write_text('// vendored helper fixture\n')
    source = {'repository': 'https://github.com/nicodes/cicd', 'revision': 'a' * 40,
              'files': {'helpers/pins.mjs': hashlib.sha256(helper.read_bytes()).hexdigest()}}
    (engineering / 'SOURCE.json').write_text(json.dumps(source, indent=2) + '\n')
    (root / '.mise.toml').write_text('[tools]\nbun = "1.4.1"\n')
    for name in applications:
        directory = root / name
        directory.mkdir(parents=True)
        (directory / 'package.json').write_text('{"packageManager": "bun@1.4.1"}')
        (directory / 'bun.lock').write_text('')
    workflows = root / '.github/workflows'
    workflows.mkdir(parents=True)
    (workflows / 'bun-updates.yml').write_text(
        'name: Bun updates\non:\n  schedule:\n    - cron: \'17 10 * * 1\'\n'
        'permissions:\n  contents: read\njobs:\n  update:\n'
        "    if: github.ref == 'refs/heads/main'\n"
        '    permissions:\n      contents: read\n      issues: write\n    steps:\n'
        f'      - uses: {MISE}\n      - run: python3 scripts/engineering/helpers/update-bun.py\n')
    (root / '.github/dependabot.yml').write_text(
        'version: 2\nupdates:\n  - package-ecosystem: github-actions\n'
        '    directory: /\n    schedule:\n      interval: weekly\n'
        '  - package-ecosystem: docker\n    directory: /deploy/images\n'
        '    schedule:\n      interval: weekly\n')
    (workflows / 'ci.yml').write_text('name: CI\non: [push]\njobs:\n  ci:\n'
                                      '    runs-on: ubuntu-24.04\n    steps:\n'
                                      + ''.join(f'      - uses: {use}\n' for use in steps))
    if composite is not None:
        action = root / '.github/actions/publish/action.yml'
        action.parent.mkdir(parents=True)
        action.write_text('runs:\n  using: composite\n  steps:\n'
                          + ''.join(f'    - uses: {use}\n' for use in composite))
    if record is not None:
        (engineering / 'ACTION-PINS.json').write_text(json.dumps(record, indent=2) + '\n')
    subprocess.run(['git', 'init', '-q'], cwd=root, check=True, timeout=60)
    subprocess.run(['git', 'add', '-A'], cwd=root, check=True, timeout=60)


def stage(root):
    subprocess.run(['git', 'add', '-A'], cwd=root, check=True, timeout=60)


def run_pins(root):
    return subprocess.run(['bun', str(PINS)], cwd=root, capture_output=True, text=True, timeout=60)


def run_updater(root, arguments, remote=LS_REMOTE):
    with tempfile.TemporaryDirectory() as directory:
        shim = Path(directory) / 'git'
        shim.write_text(f'#!/bin/sh\ncat <<EOF\n{remote}EOF\n')
        shim.chmod(0o700)
        env = {**os.environ, 'PATH': f'{shim.parent}{os.pathsep}{os.environ["PATH"]}'}
        return subprocess.run(['bun', str(UPDATER), *arguments], cwd=root,
                              capture_output=True, text=True, timeout=60, env=env)


class ActionPinGuard(unittest.TestCase):
    def test_matching_pins_and_record_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_product(root,
                          steps=[f'nicodes/komizo-actions/connect@{V3} # v0.0.3'],
                          composite=[f'nicodes/komizo-actions/publish-config@{V1} # v0.0.1'],
                          record={'repository': 'https://github.com/nicodes/komizo-actions',
                                  'pins': {'connect': {'tag': 'v0.0.3', 'sha': V3},
                                           'publish-config': {'tag': 'v0.0.1', 'sha': V1}}})
            result = run_pins(root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_drifted_pin_is_refused_with_both_revisions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_product(root, steps=[f'nicodes/komizo-actions/connect@{V8} # v0.0.8'],
                          record={'repository': 'https://github.com/nicodes/komizo-actions',
                                  'pins': {'connect': {'tag': 'v0.0.3', 'sha': V3}}})
            result = run_pins(root)
            self.assertNotEqual(result.returncode, 0)
            output = result.stdout + result.stderr
            for expected in ['connect', V8, V3, 'v0.0.3', 'ACTION-PINS.json']:
                self.assertIn(expected, output)

    def test_missing_record_is_refused_when_actions_are_used(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_product(root, steps=[f'nicodes/komizo-actions/connect@{V3} # v0.0.3'])
            result = run_pins(root)
            self.assertNotEqual(result.returncode, 0)
            output = result.stdout + result.stderr
            self.assertIn('ACTION-PINS.json', output)
            self.assertIn('action-pins.mjs', output)

    def test_unrecorded_action_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_product(root, steps=[f'nicodes/komizo-actions/deploy@{V3} # v0.0.3'],
                          record={'repository': 'https://github.com/nicodes/komizo-actions',
                                  'pins': {'connect': {'tag': 'v0.0.3', 'sha': V3}}})
            result = run_pins(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('deploy', result.stdout + result.stderr)

    def test_non_sha_reference_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_product(root, steps=['nicodes/komizo-actions/connect@v0.0.1'],
                          record={'repository': 'https://github.com/nicodes/komizo-actions',
                                  'pins': {'connect': {'tag': 'v0.0.1', 'sha': V1}}})
            result = run_pins(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('full commit SHA', result.stdout + result.stderr)

    def test_tag_object_sha_is_refused_against_the_commit_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_product(root, steps=[f'nicodes/komizo-actions/connect@{V3_TAG} # v0.0.3'],
                          record={'repository': 'https://github.com/nicodes/komizo-actions',
                                  'pins': {'connect': {'tag': 'v0.0.3', 'sha': V3}}})
            result = run_pins(root)
            self.assertNotEqual(result.returncode, 0)
            output = result.stdout + result.stderr
            self.assertIn(V3_TAG, output)
            self.assertIn(V3, output)

    def test_products_without_komizo_actions_need_no_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_product(root, steps=[CHECKOUT])
            result = run_pins(root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_malformed_record_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_product(root, steps=[f'nicodes/komizo-actions/connect@{V3} # v0.0.3'],
                          record={'repository': 'https://github.com/nicodes/komizo-actions',
                                  'pins': {'connect': {'tag': 'v0.0.3', 'sha': 'dd77ee87'}}})
            result = run_pins(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('peeled commit SHA', result.stdout + result.stderr)

    def test_misspelled_portfolio_org_is_refused_naming_the_correction(self):
        # One-byte org typos pass the full-SHA rule but resolve to nothing.
        for typo, correction in [('nicode', 'nicodes'), ('Nicodes', 'nicodes'),
                                 ('aviorstudi', 'aviorstudio'), ('astrylogica', 'astrylogical')]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_product(root, steps=[f'{typo}/some-action@{V3}'])
                result = run_pins(root)
                self.assertNotEqual(result.returncode, 0, typo)
                output = result.stdout + result.stderr
                self.assertIn(typo, output)
                self.assertIn(correction, output)
                self.assertIn('misspelling', output)

    def test_misspelled_fleet_repository_is_refused_naming_the_correction(self):
        # The komizo-be incident (fd23b9c9): nicodes/komozo-actions — 0x6f for
        # 0x69 — satisfied the generic full-SHA rule while pointing at nothing,
        # and every CD run failed at action resolution with CI blind to it.
        for typo, correction in [('nicodes/komozo-actions', 'nicodes/komizo-actions'),
                                 ('nicodes/cicdd', 'nicodes/cicd'),
                                 ('aviorstudio/gdam-action', 'aviorstudio/gdam-actions')]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_product(root, steps=[f'{typo}/some-action@{V3}'])
                result = run_pins(root)
                self.assertNotEqual(result.returncode, 0, typo)
                output = result.stdout + result.stderr
                self.assertIn(typo, output)
                self.assertIn(correction, output)

    def test_exact_fleet_and_distant_third_party_references_are_unaffected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_product(root, steps=['aviorstudio/gdam-actions/install@'+V3,
                                       f'nicodes/cicd/.github/workflows/deployed.yml@{V3}',
                                       'nico/some-action@'+V3,  # three edits from nicodes
                                       'docker/login-action@'+V1])
            result = run_pins(root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ApplicationManifestScan(unittest.TestCase):
    """The per-application rules follow every tracked top-level `*/package.json`
    directory — `app/` is convention, not requirement — and a repository with no
    Bun application at all (a Godot/game repo) has nothing to check in that loop
    while the mise exact-version and workflow/action-pins rules still apply."""

    def test_app_directory_repo_passes_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_product(root, steps=[CHECKOUT])
            result = run_pins(root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_non_app_manifest_directory_passes(self):
        # aviorstudio/fieldsofrevik tracks no `app/`; its Bun app is `playwright/`.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_product(root, steps=[CHECKOUT], applications=('playwright',))
            result = run_pins(root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_every_tracked_manifest_is_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_product(root, steps=[CHECKOUT], applications=('app', 'playwright'))
            (root / 'playwright/package.json').write_text('{"packageManager": "bun@1.3.9"}')
            stage(root)
            result = run_pins(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('playwright: Bun pin differs', result.stdout + result.stderr)

    def test_manifestless_repo_passes_with_other_pins_still_enforced(self):
        # Pure Godot/game shape: no package.json anywhere, so the applications
        # loop is skipped — but mise and workflow pins keep failing loudly.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_product(root, steps=[CHECKOUT], applications=())
            result = run_pins(root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_product(root, steps=['actions/checkout@v4'], applications=())
            result = run_pins(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('full commit SHA', result.stdout + result.stderr)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_product(root, steps=[CHECKOUT], applications=())
            (root / '.mise.toml').write_text('[tools]\nbun = "1"\n')
            stage(root)
            result = run_pins(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('not an exact version', result.stdout + result.stderr)

    def test_missing_lockfile_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_product(root, steps=[CHECKOUT], applications=('playwright',))
            (root / 'playwright/bun.lock').unlink()
            stage(root)
            result = run_pins(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('playwright: commit bun.lock', result.stdout + result.stderr)

    def test_competing_lockfile_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_product(root, steps=[CHECKOUT], applications=('playwright',))
            (root / 'playwright/package-lock.json').write_text('{}')
            stage(root)
            result = run_pins(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('playwright: remove the competing package-lock.json', result.stdout + result.stderr)


class ActionPinUpdater(unittest.TestCase):
    def call(self, function, arguments, map_arguments=()):
        program = (f'import {{ {function} }} from {json.dumps(UPDATER.as_uri())};\n'
                   'const args = JSON.parse(process.argv[1]);\n'
                   f'for (const index of {json.dumps(list(map_arguments))}) '
                   'args[index] = new Map(Object.entries(args[index]));\n'
                   'try {\n'
                   f'  let value = {function}(...args);\n'
                   '  if (value instanceof Map) value = Object.fromEntries(value);\n'
                   "  console.log('OK ' + JSON.stringify(value));\n"
                   '} catch (error) {\n'
                   "  console.log('ERR ' + (error.message ?? String(error)));\n"
                   '}\n')
        result = subprocess.run(['bun', '--eval', program, json.dumps(list(arguments))],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith(('OK ', 'ERR ')), result.stdout)
        return result.stdout[3:].rstrip('\n'), result.stdout.startswith('OK ')

    def test_annotated_tags_resolve_to_peeled_commits(self):
        value, ok = self.call('parseTags', [LS_REMOTE])
        self.assertTrue(ok, value)
        tags = json.loads(value)
        self.assertEqual(tags['v0.0.3']['commit'], V3)
        self.assertEqual(tags['v0.0.3']['object'], V3_TAG)
        self.assertEqual(tags['v0.0.10']['commit'], V10)
        self.assertEqual(tags['v0.0.1']['commit'], V1)
        self.assertEqual(self.call('latestTag', [tags], map_arguments=(0,)), ('"v0.0.10"', True))

    def test_lightweight_and_unknown_ls_remote_output(self):
        lightweight = 'f00d' + '0' * 36
        value, ok = self.call('parseTags', [f'{lightweight}\trefs/tags/light\n'])
        self.assertTrue(ok, value)
        tags = json.loads(value)
        self.assertEqual(tags['light']['commit'], lightweight)
        self.assertEqual(tags['light']['object'], lightweight)
        for output, expected in [('', False), ('not-ls-remote\n', False),
                                 (f'{lightweight}\trefs/tags/x\n{V1}\trefs/tags/x^{{}}\n', True)]:
            value, ok = self.call('parseTags', [output])
            self.assertEqual(ok, expected, output)

    def test_record_schema(self):
        valid = {'repository': 'https://github.com/nicodes/komizo-actions',
                 'pins': {'connect': {'tag': 'v0.0.10', 'sha': V10}}}
        value, ok = self.call('parseRecord', [json.dumps(valid)])
        self.assertTrue(ok, value)
        for broken in [{**valid, 'repository': 'https://example.invalid'},
                       {**valid, 'pins': None},
                       {'repository': valid['repository'], 'pins': {'connect': {'tag': 'main', 'sha': V10}}},
                       {'repository': valid['repository'], 'pins': {'connect': {'tag': 'v0.0.10', 'sha': 'dd77'}}}]:
            value, ok = self.call('parseRecord', [json.dumps(broken)])
            self.assertFalse(ok, broken)

    def test_record_rendering_is_canonical(self):
        pins = {'run-task': {'sha': V2, 'tag': 'v0.0.2'}, 'connect': {'sha': V10, 'tag': 'v0.0.10'}}
        value, ok = self.call('renderRecord', [pins])
        self.assertTrue(ok, value)
        rendered = json.loads(value)
        self.assertEqual(json.loads(rendered)['pins']['connect']['sha'], V10)
        self.assertLess(rendered.index('connect'), rendered.index('run-task'))
        value_again, _ = self.call('renderRecord', [json.loads(rendered)['pins']])
        self.assertEqual(rendered, json.loads(value_again))

    def test_workflow_scanning_and_rewriting(self):
        workflow = (f'name: CI\njobs:\n  deploy:\n    steps:\n'
                    f'      - uses: nicodes/komizo-actions/connect@{V3_TAG} # v0.0.3\n'
                    f'      - uses: nicodes/komizo-actions/deploy@{V3}\n'
                    f'      - uses: nicodes/komizo-actions/connect@{V10} # v0.0.10\n'
                    f'      - {CHECKOUT}\n')
        value, ok = self.call('scanUses', [workflow])
        self.assertTrue(ok, value)
        self.assertEqual(json.loads(value), [
            {'action': 'connect', 'ref': V3_TAG},
            {'action': 'deploy', 'ref': V3},
            {'action': 'connect', 'ref': V10}])
        pins = {'connect': {'tag': 'v0.0.10', 'sha': V10}, 'deploy': {'tag': 'v0.0.3', 'sha': V3}}
        value, ok = self.call('rewriteUses', [workflow, pins])
        self.assertTrue(ok, value)
        result = json.loads(value)
        self.assertEqual(result['changes'], 2)
        self.assertNotIn(V3_TAG, result['text'])
        self.assertIn(f'nicodes/komizo-actions/connect@{V10} # v0.0.10', result['text'])
        self.assertIn(f'nicodes/komizo-actions/deploy@{V3} # v0.0.3', result['text'])
        self.assertIn(CHECKOUT, result['text'])
        value, ok = self.call('rewriteUses', [result['text'], pins])
        self.assertEqual(json.loads(value)['changes'], 0)

    def test_drift_detection(self):
        tags = json.loads(self.call('parseTags', [LS_REMOTE])[0])
        aligned = {'repository': 'x', 'pins': {'connect': {'tag': 'v0.0.3', 'sha': V3}}}
        uses = [{'file': '.github/workflows/ci.yml', 'action': 'connect', 'ref': V3}]
        value, ok = self.call('drift', [aligned, tags, uses], map_arguments=(1,))
        self.assertTrue(ok, value)
        self.assertEqual(json.loads(value), [])
        moved = {'repository': 'x', 'pins': {'connect': {'tag': 'v0.0.3', 'sha': V8}}}
        vanished = {'repository': 'x', 'pins': {'deploy': {'tag': 'v0.0.6', 'sha': V1}}}
        for record, expected in [(moved, 'moved upstream'), (vanished, 'no longer exists')]:
            value, ok = self.call('drift', [record, tags, uses], map_arguments=(1,))
            self.assertTrue(ok)
            self.assertIn(expected, value)
        value, ok = self.call('drift', [aligned, tags,
                              [{'file': 'ci.yml', 'action': 'connect', 'ref': V1},
                               {'file': 'ci.yml', 'action': 'run-task', 'ref': V2}]], map_arguments=(1,))
        self.assertTrue(ok)
        self.assertIn('not recorded', value)
        self.assertIn('differs from the recorded', value)


class ActionPinUpdaterEndToEnd(unittest.TestCase):
    def test_check_bootstrap_update_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_product(root, steps=[f'nicodes/komizo-actions/connect@{V3} # v0.0.3',
                                       f'nicodes/komizo-actions/deploy@{V1_TAG} # v0.0.1'])
            record = root / 'scripts/engineering/ACTION-PINS.json'
            missing = run_updater(root, ['--check'])
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn('missing', missing.stdout + missing.stderr)

            bootstrapped = run_updater(root, [])
            self.assertEqual(bootstrapped.returncode, 0, bootstrapped.stdout + bootstrapped.stderr)
            pins = json.loads(record.read_text())['pins']
            self.assertEqual(pins['connect'], {'tag': 'v0.0.10', 'sha': V10})
            self.assertEqual(pins['deploy'], {'tag': 'v0.0.10', 'sha': V10})

            drifting = run_updater(root, ['--check'])
            self.assertNotEqual(drifting.returncode, 0)
            self.assertIn('differs from the recorded', drifting.stdout)

            updated = run_updater(root, ['--update'])
            self.assertEqual(updated.returncode, 0, updated.stdout + updated.stderr)
            workflow = (root / '.github/workflows/ci.yml').read_text()
            self.assertIn(f'nicodes/komizo-actions/connect@{V10} # v0.0.10', workflow)
            self.assertIn(f'nicodes/komizo-actions/deploy@{V10} # v0.0.10', workflow)
            self.assertNotIn(V3, workflow)

            aligned = run_updater(root, ['--check'])
            self.assertEqual(aligned.returncode, 0, aligned.stdout + aligned.stderr)

    def test_moved_upstream_tag_requires_explicit_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            moved_remote = LS_REMOTE.replace(
                f'{V3}\trefs/tags/v0.0.3^{{}}\n', f'{V8}\trefs/tags/v0.0.3^{{}}\n')
            write_product(root, steps=[f'nicodes/komizo-actions/connect@{V3} # v0.0.3'],
                          record={'repository': 'https://github.com/nicodes/komizo-actions',
                                  'pins': {'connect': {'tag': 'v0.0.3', 'sha': V3}}})
            refused = run_updater(root, ['--check'], moved_remote)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn('moved upstream', refused.stdout)
            blocked = run_updater(root, [], moved_remote)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn('--accept-moved-tags', blocked.stdout + blocked.stderr)
            accepted = run_updater(root, ['--accept-moved-tags'], moved_remote)
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            pins = json.loads((root / 'scripts/engineering/ACTION-PINS.json').read_text())['pins']
            self.assertEqual(pins['connect']['sha'], V8)
