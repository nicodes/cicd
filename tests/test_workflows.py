"""Structural contract for the shared reusable vulnerability-scan and tool-watch workflows.

The repository parses YAML with Bun (helpers/pins.mjs), so these tests load the
workflow documents through `bun -e` and assert on the resulting JSON. They pin
the contract the product callers depend on: trigger shape, inputs, permissions,
pinned actions, artifact retention and concurrency semantics.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).parents[1]
WORKFLOWS = ROOT/'.github'/'workflows'
HELPER_REF = 'a170e0fde8a98744e9737847a653df926242a3c9'
USE_KEY = re.compile(r'(?:^|[-\s])uses:\s*(?P<value>.+?)\s*$')
SHA_PIN = re.compile(r'^[\w.-]+(?:/[\w.-]+)+@[0-9a-f]{40}\s+#\s*v?\d[\w.+-]*$')


def load(name):
    script = ('console.log(JSON.stringify(Bun.YAML.parse('
              'require("fs").readFileSync(process.env.WORKFLOW_DOC, "utf8"))))')
    document = subprocess.check_output(['bun', '-e', script], text=True,
                                       env={**os.environ, 'WORKFLOW_DOC': str(WORKFLOWS/name)})
    return json.loads(document)


def run_steps(job):
    return [step['run'] for step in job['steps'] if 'run' in step]


def checkout_of(job, repository):
    return next(step for step in job['steps']
                if step.get('uses', '').startswith('actions/checkout')
                and step.get('with', {}).get('repository') == repository)


class VulnerabilityScanWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.document = load('vuln.yml')
        self.scan = self.document['jobs']['scan']
        self.report = self.document['jobs']['report']

    def test_the_only_trigger_is_workflow_call(self):
        self.assertEqual(list(self.document['on']), ['workflow_call'])

    def test_project_is_the_only_required_input(self):
        inputs = self.document['on']['workflow_call']['inputs']
        self.assertEqual(inputs['project']['type'], 'string')
        self.assertTrue(inputs['project']['required'])
        self.assertEqual(set(inputs), {'project', 'repository', 'source-scan-command'})
        self.assertEqual(inputs['repository']['type'], 'string')
        self.assertFalse(inputs['repository']['required'])
        self.assertEqual(inputs['repository']['default'], '')
        self.assertEqual(inputs['source-scan-command']['type'], 'string')
        self.assertFalse(inputs['source-scan-command']['required'])
        self.assertEqual(inputs['source-scan-command']['default'], '')

    def test_scan_job_keeps_the_fleet_runner_and_read_permissions(self):
        self.assertEqual(self.scan['runs-on'], 'ubuntu-24.04')
        self.assertEqual(self.scan['timeout-minutes'], 110)
        self.assertEqual(self.scan['permissions'],
                         {'contents': 'read', 'packages': 'read', 'deployments': 'read'})

    def test_deployed_scan_runs_the_callers_vendored_helper_for_the_project(self):
        deployed = next(step for step in self.scan['steps'] if 'run' in step
                        and 'scan-deployed.py' in step['run'])
        self.assertEqual(deployed['run'],
                         'python3 scripts/engineering/helpers/scan-deployed.py --project ${{ inputs.project }}'
                         ' --repository "$SCAN_DEPLOYED_REPOSITORY"')
        self.assertEqual(deployed['env'], {'GH_TOKEN': '${{ github.token }}',
                                           'SCAN_DEPLOYED_REPOSITORY': '${{ inputs.repository }}'})

    def test_optional_source_scan_step_skips_when_no_command_is_given(self):
        source = next(step for step in self.scan['steps'] if step.get('name') == 'Scan every source module')
        self.assertEqual(source['if'], "inputs.source-scan-command != ''")
        self.assertEqual(source['run'], '${{ inputs.source-scan-command }}')

    def test_registry_login_targets_ghcr_with_the_caller_token(self):
        login = next(step for step in self.scan['steps']
                     if step.get('uses', '').startswith('docker/login-action'))
        self.assertEqual(login['with']['registry'], 'ghcr.io')
        self.assertEqual(login['with']['username'], '${{ github.actor }}')
        self.assertEqual(login['with']['password'], '${{ github.token }}')

    def test_report_job_is_a_separate_issue_writer_using_cicds_own_helper(self):
        self.assertEqual(self.report['needs'], ['scan'])
        self.assertEqual(self.report['if'], 'failure()')
        self.assertEqual(self.report['permissions'], {'contents': 'read', 'issues': 'write'})
        checkout = checkout_of(self.report, 'nicodes/cicd')
        self.assertEqual(checkout['with']['ref'], HELPER_REF)
        self.assertEqual(checkout['with']['path'], '.cicd')
        self.assertIs(checkout['with']['persist-credentials'], False)
        self.assertEqual(checkout['with']['sparse-checkout'], 'helpers/')
        self.assertIn('python3 .cicd/helpers/report-failure.py', run_steps(self.report))
        report_run = next(step for step in self.report['steps'] if 'run' in step)
        self.assertEqual(report_run['env'], {'GH_TOKEN': '${{ github.token }}'})


class ToolWatchWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.document = load('tools.yml')
        self.watch = self.document['jobs']['watch']

    def test_the_only_trigger_is_workflow_call_with_no_inputs(self):
        self.assertEqual(self.document['on'], {'workflow_call': None})
        self.assertIsNone(self.document['on']['workflow_call'])

    def test_watch_stays_main_only(self):
        self.assertEqual(self.watch['if'], "github.ref == 'refs/heads/main'")

    def test_concurrency_group_never_cancels_a_running_watch(self):
        self.assertEqual(self.watch['concurrency'],
                         {'group': 'tool-watch', 'cancel-in-progress': False})
        self.assertEqual(self.watch['timeout-minutes'], 40)

    def test_watch_permissions_are_read_plus_issues_only(self):
        self.assertEqual(self.watch['permissions'], {'contents': 'read', 'issues': 'write'})

    def test_the_callers_checkout_persists_no_credentials(self):
        caller = next(step for step in self.watch['steps']
                      if step.get('uses', '').startswith('actions/checkout')
                      and 'repository' not in step.get('with', {}))
        self.assertIs(caller['with']['persist-credentials'], False)

    def test_watch_runs_the_callers_vendored_helper_with_the_caller_token(self):
        self.assertIn('python3 scripts/engineering/helpers/watch-tools.py', run_steps(self.watch))
        watch_run = next(step for step in self.watch['steps'] if 'run' in step)
        self.assertEqual(watch_run['env'], {'GH_TOKEN': '${{ github.token }}'})
        self.assertEqual(watch_run['timeout-minutes'], 12)

    def test_update_inventory_artifact_is_retained_thirty_days_whenever_set(self):
        artifact = next(step for step in self.watch['steps']
                        if step.get('uses', '').startswith('actions/upload-artifact'))
        self.assertEqual(artifact['if'], 'always()')
        self.assertEqual(artifact['with'],
                         {'name': 'tool-updates', 'path': '.artifacts/tool-updates.json',
                          'if-no-files-found': 'ignore', 'retention-days': 30})

    def test_failure_reporting_uses_cicds_own_helper_at_the_pinned_revision(self):
        checkout = checkout_of(self.watch, 'nicodes/cicd')
        self.assertEqual(checkout['with']['ref'], HELPER_REF)
        self.assertEqual(checkout['with']['sparse-checkout'], 'helpers/')
        failure = next(step for step in self.watch['steps'] if step.get('if') == 'failure()')
        self.assertEqual(failure['run'], 'python3 .cicd/helpers/report-failure.py')
        self.assertEqual(failure['env'], {'GH_TOKEN': '${{ github.token }}'})


class WorkflowPinTests(unittest.TestCase):
    def test_every_uses_reference_is_a_full_sha_with_a_version_comment(self):
        for path in sorted(WORKFLOWS.glob('*.yml')):
            with self.subTest(workflow=path.name):
                entries = []
                for line in path.read_text().splitlines():
                    if line.strip().startswith('#'):
                        continue
                    match = USE_KEY.search(line)
                    if match:
                        entries.append(match.group('value').strip())
                if not entries:
                    # Pure gh-api workflows (deployed.yml) carry no actions;
                    # the pin rule is vacuously satisfied.
                    continue
                for value in entries:
                    self.assertRegex(value, SHA_PIN, f'{path.name}: uses {value}')


class CallerContractFloorTests(unittest.TestCase):
    """Called workflows can only restrict, never elevate, the caller's grant:
    an under-granted caller startup_failures the run with zero jobs
    (live matrix 2026-09-20, nicodes/cicd: tools 35483270243 vs 35483270273;
    vuln 35483270307 vs 35483270237). The README caller contracts must
    therefore publish a permission floor covering every called job."""

    def readme_slice(self, begin, end):
        text = (ROOT/'README.md').read_text()
        return ' '.join(text[text.index(begin):text.index(end)].split())

    def test_vuln_floor_covers_every_called_job_permission(self):
        document = load('vuln.yml')
        floor = set()
        for job in document['jobs'].values():
            floor.update(job['permissions'])
        self.assertEqual(floor, {'contents', 'packages', 'deployments', 'issues'})
        section = self.readme_slice('### Caller contract: vulnerability scan',
                                    '### Caller contract: tool watch')
        for scope in ('contents: read', 'packages: read',
                      'deployments: read', 'issues: write'):
            self.assertIn(scope, section, scope)
        self.assertIn('zero jobs', section)

    def test_tools_floor_covers_every_called_job_permission(self):
        watch = load('tools.yml')['jobs']['watch']
        self.assertEqual(set(watch['permissions']), {'contents', 'issues'})
        section = self.readme_slice('### Caller contract: tool watch',
                                    '### What this repository changed')
        self.assertIn('contents: read', section)
        self.assertIn('issues: write', section)
        self.assertIn('zero jobs', section)


class ToolMaintenanceRenameTests(unittest.TestCase):
    def test_own_maintenance_workflow_survives_under_its_new_name(self):
        document = load('tool-maintenance.yml')
        self.assertEqual(document['name'], 'Tool maintenance')
        self.assertEqual(document['on']['schedule'], [{'cron': '31 9 * * 1'}])
        self.assertIn('python3 helpers/watch-tools.py', run_steps(document['jobs']['watch']))


if __name__ == '__main__':
    unittest.main()
