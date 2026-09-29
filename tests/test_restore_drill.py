import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('restore_drill', Path(__file__).parents[1]/'helpers/restore-drill.py')
drill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drill)


class RestoreBoundaries(unittest.TestCase):
    def test_registry_pull_requires_authenticated_product_and_image_identity(self):
        evidence = {'image_reference': 'ghcr.io/nicodes/wrong-db:'+'a'*40, 'image_id': 'sha256:'+'b'*64}
        snapshot = SimpleNamespace(unseal=lambda *_: evidence)
        receiver = SimpleNamespace(receive=lambda *_: None)
        def helper(name):
            return snapshot if name == 'snapshot' else receiver
        with patch.object(drill, 'helper', helper), patch.object(drill, 'docker') as docker:
            with self.assertRaisesRegex(ValueError, 'product'):
                drill.drill('ormos', Path('/unused'), Path('/unused'), pull=True)
            docker.assert_not_called()
        evidence['image_reference'] = 'ghcr.io/nicodes/ormos-db:'+'a'*40
        calls = []
        def docker(*args, **kwargs):
            calls.append(args)
            return json.dumps([{'Id': 'sha256:'+'c'*64}]) if args[:2] == ('image', 'inspect') else ''
        with patch.object(drill, 'helper', helper), patch.object(drill, 'docker', docker):
            with self.assertRaisesRegex(ValueError, 'differs'):
                drill.drill('ormos', Path('/unused'), Path('/unused'), pull=True)
        self.assertEqual(calls, [('pull', '--quiet', evidence['image_reference']),
                                 ('image', 'inspect', evidence['image_reference'])])

    def test_timeout_and_failures_never_disclose_clone_credentials(self):
        secret = 'disposable-secret-that-must-not-appear'
        with patch.object(drill.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['docker', 'run', secret], 1)):
            with self.assertRaises(RuntimeError) as raised:
                drill.docker('run', secret)
            self.assertNotIn(secret, str(raised.exception))
            self.assertTrue(raised.exception.__suppress_context__)
        with patch.object(drill.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, secret, secret)):
            with self.assertRaises(RuntimeError) as raised:
                drill.docker('run', secret)
            self.assertNotIn(secret, str(raised.exception))

    def test_new_product_backup_namespace_is_authenticated_before_any_pull(self):
        for project, owner, component in [('gdam','aviorstudio','db'), ('termcade','aviorstudio','db'), ('astry','astrylogical','pb')]:
            evidence = {'image_reference': f'ghcr.io/nicodes/{project}-{component}:'+'a'*40, 'image_id':'sha256:'+'b'*64}
            snapshot = SimpleNamespace(unseal=lambda *_: evidence)
            receiver = SimpleNamespace(receive=lambda *_: None)
            with patch.object(drill, 'helper', lambda name: snapshot if name == 'snapshot' else receiver), patch.object(drill,'docker') as docker:
                with self.assertRaisesRegex(ValueError,'product'):
                    drill.drill(project,Path('/unused'),Path('/unused'),pull=True)
                docker.assert_not_called()
            evidence['image_reference'] = f'ghcr.io/{owner}/{project}-{component}:'+'a'*40
            calls = []
            def docker(*args, **kwargs):
                calls.append(args)
                return json.dumps([{'Id':'sha256:'+'c'*64}]) if args[:2] == ('image','inspect') else ''
            with patch.object(drill,'helper',lambda name: snapshot if name == 'snapshot' else receiver), patch.object(drill,'docker',docker):
                with self.assertRaisesRegex(ValueError,'differs'):
                    drill.drill(project,Path('/unused'),Path('/unused'),pull=True)
            self.assertEqual(calls, [('pull','--quiet',evidence['image_reference']),('image','inspect',evidence['image_reference'])])


def postgres_manifest(project='cazper', revision='a'*40, api_id='sha256:'+'c'*64):
    return {'project': project, 'revision': revision,
            'images': {'api': {'reference': f'ghcr.io/nicodes/{project}-api:{revision}', 'image_id': api_id},
                       'gate': {'reference': f'ghcr.io/nicodes/{project}-gate:{revision}', 'image_id': 'sha256:'+'c'*64}}}


class PostgresRestoreBoundaries(unittest.TestCase):
    """The PostgreSQL drill authenticates the same things the PocketBase one does.

    Each case stops the drill BEFORE any container starts, so these run
    without Docker; the end-to-end path is exercised by test-restore-drill.py
    against the product's real images.
    """

    def drill_with(self, evidence, project='cazper', docker=None):
        snapshot = SimpleNamespace(unseal=lambda *_: evidence)
        receiver = SimpleNamespace(receive=lambda *_: None)
        recovery = SimpleNamespace()
        helpers = {'snapshot': snapshot, 'receive-backup': receiver, 'postgres-recovery': recovery}
        with patch.object(drill, 'helper', helpers.get), patch.object(drill, 'docker', docker or (lambda *a, **k: '')):
            return drill.drill(project, Path('/unused'), Path('/unused'), pull=True)

    def test_cazper_takes_the_postgresql_path(self):
        self.assertEqual(drill.BACKENDS['cazper'], 'postgresql')
        self.assertEqual(drill.BACKENDS['ormos'], 'pocketbase')

    def test_a_pocketbase_envelope_is_refused_by_a_postgresql_product(self):
        # No 'backend' key at all is how a PocketBase envelope reads.
        with self.assertRaisesRegex(ValueError, 'not a PostgreSQL snapshot'):
            self.drill_with({'image_reference': 'ghcr.io/nicodes/cazper-db:'+'a'*40})

    def test_a_snapshot_of_another_product_is_refused(self):
        evidence = {'backend': 'postgresql', 'postgresql': postgres_manifest(project='ormos')}
        with self.assertRaisesRegex(ValueError, 'another product'):
            self.drill_with(evidence)

    def test_an_inexact_revision_is_refused(self):
        evidence = {'backend': 'postgresql', 'postgresql': postgres_manifest(revision='host-local:release')}
        with self.assertRaisesRegex(ValueError, 'exact product revision'):
            self.drill_with(evidence)

    def test_an_application_image_that_is_not_the_snapshot_s_is_refused(self):
        # The tag resolves locally, but to different bytes than the snapshot
        # named. Booting it would prove nothing about the release the data
        # came from, so the drill refuses rather than running green.
        evidence = {'backend': 'postgresql', 'postgresql': postgres_manifest(api_id='sha256:'+'d'*64)}
        calls = []
        def docker(*args, **kwargs):
            calls.append(args)
            return json.dumps([{'Id': 'sha256:'+'c'*64}]) if args[:2] == ('image', 'inspect') else ''
        with self.assertRaisesRegex(ValueError, 'differs from authenticated'):
            self.drill_with(evidence, docker=docker)
        self.assertIn(('image', 'inspect', 'ghcr.io/nicodes/cazper-api:'+'a'*40), calls)
