import json
from pathlib import Path
import subprocess
import unittest


class DependencyCoverage(unittest.TestCase):
    def test_new_go_modules_and_disabled_schedules_cannot_escape_updates(self):
        module = (Path(__file__).parents[1] / 'helpers/dependency-coverage.mjs').as_uri()
        program = f'''import {{ verifyDependencyCoverage }} from {json.dumps(module)};
const data = JSON.parse(process.argv[1]);
verifyDependencyCoverage(data.config, data.files, data.bun);
'''
        updates = [
            {'package-ecosystem': ecosystem, 'directories': directories, 'schedule': {'interval': 'weekly'}}
            for ecosystem, directories in [('github-actions', ['/']),
                                            ('docker', ['/deploy/images']), ('gomod', ['/api', '/pb'])]
        ]

        workflow = {'on': {'schedule': [{'cron': '17 10 * * 1'}]}, 'jobs': {'update': {
            'if': "github.ref == 'refs/heads/main'", 'steps': [
                {'uses': 'jdx/mise-action@'+'a'*40},
                {'run': 'python3 scripts/engineering/helpers/update-bun.py'}]}}}

        def check(files, entries=updates, bun=workflow):
            return subprocess.run(['bun', '--eval', program, json.dumps({
                'config': {'version': 2, 'updates': entries}, 'files': files, 'bun': bun})],
                capture_output=True, text=True, timeout=20)

        self.assertEqual(check(['app/package.json']).returncode, 0)
        self.assertEqual(check(['api/go.mod', 'pb/go.mod']).returncode, 0)
        self.assertNotEqual(check(['api/go.mod', 'pb/go.mod'], updates[:-1]).returncode, 0)
        self.assertNotEqual(check(['api/go.mod', 'pb/go.mod', 'worker/nested/go.mod']).returncode, 0)
        disabled = json.loads(json.dumps(updates))
        disabled[-1]['schedule'] = {}
        self.assertNotEqual(check(['api/go.mod'], disabled).returncode, 0)
        for index in range(2):
            self.assertNotEqual(check([], updates[:index] + updates[index+1:]).returncode, 0)

        self.assertNotEqual(check([], bun={}).returncode, 0)
        workflow['jobs']['update']['environment'] = 'production'
        self.assertNotEqual(check([]).returncode, 0)
