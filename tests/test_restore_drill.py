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
