import json
from pathlib import Path
import subprocess
import unittest


class DependencyCoverage(unittest.TestCase):
    def test_new_go_modules_and_disabled_schedules_cannot_escape_updates(self):
        module = (Path(__file__).parents[1] / 'helpers/dependency-coverage.mjs').as_uri()
        program = f'''import {{ verifyDependencyCoverage }} from {json.dumps(module)};
const data = JSON.parse(process.argv[1]);
verifyDependencyCoverage(data.config, data.files);
'''
        updates = [
            {'package-ecosystem': ecosystem, 'directories': directories, 'schedule': {'interval': 'weekly'}}
            for ecosystem, directories in [('github-actions', ['/']), ('bun', ['/app']),
                                            ('docker', ['/deploy/images']), ('gomod', ['/api', '/pb'])]
        ]

        def check(files, entries=updates):
            return subprocess.run(['bun', '--eval', program, json.dumps({
                'config': {'version': 2, 'updates': entries}, 'files': files})],
                capture_output=True, text=True, timeout=20)

        self.assertEqual(check(['app/package.json']).returncode, 0)
        self.assertEqual(check(['api/go.mod', 'pb/go.mod']).returncode, 0)
        self.assertNotEqual(check(['api/go.mod', 'pb/go.mod'], updates[:-1]).returncode, 0)
        self.assertNotEqual(check(['api/go.mod', 'pb/go.mod', 'worker/nested/go.mod']).returncode, 0)
        disabled = json.loads(json.dumps(updates))
        disabled[-1]['schedule'] = {}
        self.assertNotEqual(check(['api/go.mod'], disabled).returncode, 0)
        for index in range(3):
            self.assertNotEqual(check([], updates[:index] + updates[index+1:]).returncode, 0)
