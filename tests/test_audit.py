import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('audit', Path(__file__).parents[1]/'helpers/audit.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class VerifiedRepairs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name)
        self.package = self.app/'node_modules/image-size'
        self.package.mkdir(parents=True)
        (self.package/'package.json').write_text(json.dumps({'version':'1.2.1'}))
        (self.package/'parser.js').write_text('reviewed repair')
        (self.app/'fix.patch').write_text('reviewed patch')
        self.repair = {'owner':'nicodes','rationale':'fixture','package':'image-size','version':'1.2.1',
            'review_date':str(datetime.date.today()+datetime.timedelta(days=1)),
            'patch':'fix.patch','patch_sha256':hashlib.sha256(b'reviewed patch').hexdigest(),
            'installed_sha256':{'parser.js':hashlib.sha256(b'reviewed repair').hexdigest()}}

    def test_patch_file_does_not_prove_it_was_installed(self):
        (self.package/'parser.js').write_text('unpatched parser')
        with self.assertRaisesRegex(RuntimeError, 'absent or modified'):
            module.verify_repair(self.app, self.repair)

    def test_a_second_unpatched_copy_is_not_covered(self):
        nested = self.app/'node_modules/other/node_modules/image-size'
        nested.mkdir(parents=True)
        (nested/'package.json').write_text('{"version":"1.2.1"}')
        (nested/'parser.js').write_text('unpatched parser')
        with self.assertRaisesRegex(RuntimeError, 'nested'):
            module.verify_repair(self.app, self.repair)

    def test_expired_upstream_review_is_required(self):
        self.repair['review_date'] = '2020-01-01'
        with self.assertRaisesRegex(RuntimeError, 'overdue'):
            module.verify_repair(self.app, self.repair)

    def test_exact_installed_repair(self):
        module.verify_repair(self.app, self.repair)


if __name__ == '__main__':
    unittest.main()
