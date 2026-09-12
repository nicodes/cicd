import importlib.util
from contextlib import contextmanager
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
    def test_postgres_requires_explicit_complete_product_verification(self):
        evidence = {'backend': 'postgresql', 'postgresql': {'revision': 'a'*40}, 'archive_sha256': 'b'*64}
        snapshot = SimpleNamespace(unseal=lambda *_: evidence)
        receiver = SimpleNamespace(receive=lambda *_: None)
        closed = []
        @contextmanager
        def database(*args, **kwargs):
            try:
                yield {'engine': 'sha256:'+'c'*64}
            finally:
                closed.append(True)
        postgres = SimpleNamespace(validate_manifest=lambda value, *_: value,
                                   application_images=lambda *_: {'api': 'api-id', 'worker': 'worker-id', 'gate': 'gate-id', 'assets': 'assets-id'},
                                   restored_database=database)
        with patch.object(drill, 'helper', lambda name: {'snapshot': snapshot, 'receive-backup': receiver, 'postgres-recovery': postgres}[name]):
            with self.assertRaisesRegex(ValueError, 'callback'):
                drill.drill('cazper', Path('/unused'), Path('/unused'))
            self.assertEqual(closed, [])
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                drill.drill('cazper', Path('/unused'), Path('/unused'), application_check=lambda *_: {'api_boot': 'passed'})
            self.assertEqual(closed, [True])
            checks = {key: 'passed' for key in ['api_boot', 'worker_boot', 'frontend_artifact', 'application_checks', 'application_cleanup']}
            result = drill.drill('cazper', Path('/unused'), Path('/unused'), application_check=lambda *_: checks)
            self.assertEqual(result['database_restore'], 'passed')
            self.assertEqual(result['images']['worker'], 'worker-id')
            self.assertEqual(result['images']['assets'], 'assets-id')
            self.assertEqual(result['cleanup'], 'passed')
            self.assertEqual(closed, [True, True])

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
