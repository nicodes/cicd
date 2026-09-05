import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('restore_drill', Path(__file__).parents[1]/'helpers/restore-drill.py')
drill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drill)


class RestoreBoundaries(unittest.TestCase):
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
