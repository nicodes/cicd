import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('release', Path(__file__).resolve().parents[1]/'helpers/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

class ReleaseIdentity(unittest.TestCase):
    def test_artifact_identity_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory)/'images.tar.gz'
            archive.write_bytes(b'validated artifact')
            revision = 'a'*40
            refs = release.references('ctcalc', revision, ['gate', 'config'])
            manifest = {'project': 'ctcalc', 'revision': revision,
                        'images': dict.fromkeys(refs, 'sha256:'+'b'*64),
                        'archive_sha256': release.digest(archive)}
            self.assertEqual(release.validate(manifest, 'ctcalc', revision, ['gate', 'config'], archive), refs)
            for project, commit, components in [('tonesplit', revision, ['gate', 'config']),
                                               ('ctcalc', 'c'*40, ['gate', 'config']),
                                               ('ctcalc', revision, ['gate'])]:
                with self.assertRaises(ValueError):
                    release.validate(manifest, project, commit, components, archive)
            archive.write_bytes(b'replaced artifact')
            with self.assertRaises(ValueError):
                release.validate(manifest, 'ctcalc', revision, ['gate', 'config'], archive)

    def test_mutable_or_undeclared_references_are_refused(self):
        for revision, components in [('latest', ['gate']), ('a'*40, []), ('a'*40, ['gate', 'gate']), ('a'*40, ['other'])]:
            with self.assertRaises(ValueError):
                release.references('ctcalc', revision, components)
