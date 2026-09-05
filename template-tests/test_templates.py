import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class TemplateGate(unittest.TestCase):
    def test_both_archetypes_run_complete_gate_and_propagate_each_failure(self):
        templates = Path(__file__).parents[1] / 'templates'
        for archetype in ['full-stack', 'app-only']:
            with self.subTest(archetype=archetype), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copy2(templates / archetype / 'Makefile', root / 'Makefile')
                (root / 'scripts').mkdir()
                (root / 'bin').mkdir()
                mise = root / 'bin/mise'
                mise.write_text('#!/bin/sh\n[ "$1" = exec ] && [ "$2" = -- ] || exit 99\nshift 2\nexec "$@"\n')
                mise.chmod(0o700)
                env = {**os.environ, 'PATH': str(root / 'bin') + ':' + os.environ['PATH']}

                def make(*args, **extra):
                    return subprocess.run(['make', '--no-print-directory', *args], cwd=root,
                                          env={**env, **extra}, capture_output=True, text=True, timeout=20)

                self.assertEqual(make().returncode, 0)
                self.assertFalse((root / 'ran').exists(), 'default must only display help')
                self.assertNotEqual(make('check').returncode, 0, 'missing product implementation fails closed')
                for stage in ['test', 'vuln', 'build', 'e2e']:
                    (root / 'scripts' / (stage + '.sh')).write_text(
                        '#!/bin/bash\nset -eu\n'
                        f'echo {stage} >> ran\n'
                        f'[ "${{FAIL_STAGE:-}}" != {stage} ]\n')
                self.assertEqual(make('check').returncode, 0)
                self.assertEqual((root / 'ran').read_text().splitlines(), ['test', 'vuln', 'build', 'e2e'])
                for stage in ['test', 'vuln', 'build', 'e2e']:
                    self.assertNotEqual(make('check', FAIL_STAGE=stage).returncode, 0, stage)
                (root / 'ran').unlink()
                self.assertEqual(make('e2e').returncode, 0)
                self.assertEqual((root / 'ran').read_text().splitlines(), ['build', 'e2e'])
                state = root / 'pb/pb_data/keep.db'
                state.parent.mkdir(parents=True)
                state.write_text('local development state')
                self.assertEqual(make('clean').returncode, 0)
                self.assertTrue(state.exists())
