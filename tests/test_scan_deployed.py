import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('scan_deployed', Path(__file__).resolve().parents[1]/'helpers/scan-deployed.py')
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


class DeployedRevision(unittest.TestCase):
    def test_failed_latest_deploy_does_not_hide_prior_success(self):
        responses = [[{'id': 3, 'sha': 'a'*40}, {'id': 2, 'sha': 'b'*40}],
                     [{'state': 'failure'}, {'state': 'success'}], [{'state': 'success'}]]
        with patch.object(scanner, 'api', side_effect=responses):
            self.assertEqual(scanner.deployed_revisions('nicodes/ormos-be'), ['a'*40, 'b'*40])

    def test_missing_success_invalid_sha_or_protocol_fails(self):
        for responses in [[[]], [[{'id': 1, 'sha': 'main'}], [{'state': 'success'}]],
                          [[{'id': False}]], [[{'id': 1}], {}]]:
            with self.subTest(responses=responses), patch.object(scanner, 'api', side_effect=responses), self.assertRaises(ValueError):
                scanner.deployed_revisions('nicodes/ormos-be')


if __name__ == '__main__':
    unittest.main()
