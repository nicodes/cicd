import copy
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('scan_image', Path(__file__).resolve().parents[1]/'helpers/scan-image.py')
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


class ImageVerdict(unittest.TestCase):
    def report(self, finding=None):
        rows = [{'config': {'protocol_version': 'v1.0.0', 'scanner_name': 'govulncheck',
                 'scanner_version': 'v1.7.0', 'scan_level': 'symbol', 'scan_mode': 'binary'}},
                {'SBOM': {'modules': [{'path': 'example.org/app', 'version': 'v1.0.0'}]}}]
        if finding is not None:
            rows.append({'finding': finding})
        return rows

    def judge(self, rows):
        return scanner.judge_report('\n'.join(json.dumps(row) for row in rows))

    def finding(self):
        return {'osv': 'GO-2026-6355', 'fixed_version': 'v0.56.0', 'trace': [
            {'module': 'golang.org/x/crypto', 'version': 'v0.55.0',
             'package': 'golang.org/x/crypto/ssh', 'function': 'Dial'}]}

    def test_binary_symbol_blocks_without_source_position(self):
        self.assertEqual(self.judge(self.report(self.finding()))['reached'], ['GO-2026-6355'])
        finding = self.finding()
        del finding['fixed_version']
        self.assertEqual(self.judge(self.report(finding))['reached'], ['GO-2026-6355'])

    def test_module_and_package_mentions_are_reported_not_called_symbols(self):
        for frame in [{'module': 'golang.org/x/crypto', 'version': 'v0.56.0'},
                      {'module': 'golang.org/x/crypto', 'package': 'golang.org/x/crypto/openpgp'}]:
            verdict = self.judge(self.report({'osv': 'GO-2026-5932', 'trace': [frame]}))
            self.assertEqual(verdict['reached'], [])
            self.assertEqual(verdict['mentioned'], ['GO-2026-5932'])

    def test_missing_renamed_or_wrongly_typed_protocol_fails_closed(self):
        rows = self.report(self.finding())
        invalid = [[], rows[1:], rows[:1], rows + [rows[0]], rows + [{'results': {}}]]
        for key, value in [('fixed_version', False), ('trace', []), ('trace', {}), ('osv', 3)]:
            changed = copy.deepcopy(rows)
            changed[-1]['finding'][key] = value
            invalid.append(changed)
        for key in ['function', 'package', 'module']:
            changed = copy.deepcopy(rows)
            changed[-1]['finding']['trace'][0][key] = False
            invalid.append(changed)
        changed = copy.deepcopy(rows)
        changed[-1]['finding']['trace'][0]['symbol'] = changed[-1]['finding']['trace'][0].pop('function')
        invalid.append(changed)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.judge(value)
        with self.assertRaises(ValueError):
            scanner.judge_report('{"config":')


if __name__ == '__main__':
    unittest.main()
