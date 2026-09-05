import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('export_web', Path(__file__).parents[1]/'helpers/export-web.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ExportFailures(unittest.TestCase):
    def run_export(self, source, timeout=5):
        with tempfile.TemporaryDirectory() as app:
            module.export(app, 'dist', timeout, [sys.executable, '-c', source])

    def test_artifact_cannot_hide_failed_process(self):
        with self.assertRaisesRegex(RuntimeError, 'exited 7'):
            self.run_export("from pathlib import Path; p=Path('dist'); p.mkdir(); (p/'index.html').write_text('ok'); (p/'app.js').write_text('ok'); raise SystemExit(7)")

    def test_timeout_cannot_be_passed_by_partial_export(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_export("import time; from pathlib import Path; p=Path('dist'); p.mkdir(); (p/'index.html').write_text('partial'); time.sleep(30)", 0.1)

    def test_success_requires_javascript(self):
        with self.assertRaisesRegex(RuntimeError, 'JavaScript'):
            self.run_export("from pathlib import Path; p=Path('dist'); p.mkdir(); (p/'index.html').write_text('partial')")

    def test_successful_complete_export(self):
        self.run_export("from pathlib import Path; p=Path('dist'); p.mkdir(); (p/'index.html').write_text('ok'); (p/'app.js').write_text('ok')")


if __name__ == '__main__':
    unittest.main()
