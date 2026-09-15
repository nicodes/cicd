from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ForwardRestoration(unittest.TestCase):
    def test_abandoned_postgres_recovery_helpers_are_absent(self):
        for relative in (
            "helpers/postgres-recovery.py",
            "helpers/vendor-snapshot.py",
            "docs/postgresql-recovery.md",
        ):
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_established_snapshot_and_restore_helpers_remain(self):
        for relative in (
            "helpers/snapshot.py",
            "helpers/restore-drill.py",
            "helpers/receive-backup.py",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)
