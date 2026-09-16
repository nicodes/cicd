from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE = 'google.golang.org/grpc'
# Authenticated by go mod download (sum.golang.org), not copied from a scan.
REPAIR_SUMS = {
    'v1.83.2': 'h1:EManeRomTObA0BU7I8vXgg/78uE5MJ9M8B39EX2WscU=',
    'v1.83.2/go.mod': 'h1:YPI1hK3kDked6iHvgX3tR0y+nX/qpMFKhPgFsokw1S8=',
}


class CaddyLock(unittest.TestCase):
    def setUp(self):
        self.mod = (ROOT/'helpers/caddy.go.mod').read_text()
        self.sums = (ROOT/'helpers/caddy.go.sum').read_text()

    def assert_secure_lock(self, mod, sums):
        versions = re.findall(r'^\s*google\.golang\.org/grpc\s+(\S+)', mod, re.M)
        self.assertEqual(len(versions), 1)
        version = versions[0]
        release = re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)', version)
        self.assertIsNotNone(release, 'require an exact stable gRPC release')
        # On this lock's 1.83 line, .2 fixes GO-2026-6443; .1 already fixes
        # GO-2026-6348. Permit future secure upgrades, never a downgrade.
        self.assertGreaterEqual(tuple(map(int, release.groups())), (1, 83, 2))
        for suffix in ('', '/go.mod'):
            key = version + suffix
            entries = [line.split() for line in sums.splitlines()
                       if line.startswith(f'{MODULE} {key} ')]
            self.assertEqual(len(entries), 1, f'missing or duplicate sum: {key}')
            self.assertEqual(len(entries[0]), 3)
            self.assertRegex(entries[0][2], r'^h1:[A-Za-z0-9+/]{43}=$')
            if key in REPAIR_SUMS:
                self.assertEqual(entries[0][2], REPAIR_SUMS[key])

    def test_locked_grpc_has_both_security_fixes_and_complete_sums(self):
        self.assert_secure_lock(self.mod, self.sums)

    def test_vulnerable_or_unreleased_locks_are_rejected(self):
        for version in ('v1.82.1', 'v1.83.1', 'v1.83.2-dev'):
            changed = re.sub(r'(google\.golang\.org/grpc\s+)\S+',
                             lambda match: match[1] + version, self.mod)
            with self.subTest(version=version), self.assertRaises(AssertionError):
                self.assert_secure_lock(changed, self.sums)

    def test_missing_or_inconsistent_repair_sums_are_rejected(self):
        # Use the repair release fixture even after the live lock advances.
        mod = f'require (\n\t{MODULE} v1.83.2\n)\n'
        sums = ''.join(f'{MODULE} {key} {value}\n' for key, value in REPAIR_SUMS.items())
        self.assert_secure_lock(mod, sums)
        for line in sums.splitlines(keepends=True):
            for changed in (sums.replace(line, ''), sums + line,
                            sums.replace(line, line.replace('h1:', 'h1:A', 1)),
                            sums.replace(line, re.sub(r'h1:.', 'h1:A', line))):
                with self.subTest(line=line, changed=changed), self.assertRaises(AssertionError):
                    self.assert_secure_lock(mod, changed)


if __name__ == '__main__':
    unittest.main()
