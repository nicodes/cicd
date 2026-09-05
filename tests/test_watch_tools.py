import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('watch', Path(__file__).parents[1]/'helpers/watch-tools.py')
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


class ToolWatchTests(unittest.TestCase):
    def test_caddy_updates_and_unknown_module_responses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'helpers').mkdir()
            for extension in ['mod', 'sum']:
                (root/'helpers'/f'caddy.go.{extension}').write_text('fixture')
            modules = [{'Path': 'example.invalid/main', 'Main': True},
                {'Path': 'example.invalid/module', 'Version': 'v1.2.3',
                 'Update': {'Path': 'example.invalid/module', 'Version': 'v1.2.4'}}]
            with patch.object(watch.subprocess, 'check_output', return_value='\n'.join(map(json.dumps, modules))):
                self.assertEqual(watch.caddy_updates(root), [('Caddy: example.invalid/module', 'v1.2.3', 'v1.2.4')])
            for bad in ['', '{}', '{"Path":"example.invalid/module","Error":{"Err":"unavailable"}}',
                        '{"Path":"example.invalid/module","Update":{"Path":"other"}}']:
                with patch.object(watch.subprocess, 'check_output', return_value=bad):
                    with self.assertRaises(ValueError): watch.caddy_updates(root)

    def test_inherited_tools_are_not_repository_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = {'source': {'path': str(root/'.mise.toml')}, 'bump': '1.2.4'}
            parent = {'source': {'path': str(root.parent/'.mise.toml')}, 'bump': '99.0.0'}
            self.assertEqual(watch.updates(root, {'bun': '1.2.3'}, {'bun': local, 'go': parent}),
                             [('bun', '1.2.3', '1.2.4')])

    def test_unknown_versions_fail_and_downgrades_are_ignored(self):
        root = Path('/tmp/tool-watch-fixture')
        item = {'source': {'path': str(root/'.mise.toml')}, 'latest': '1.2.2'}
        self.assertEqual(watch.updates(root, {'bun': '1.2.3'}, {'bun': item}), [])
        item['latest'] = 'latest'
        with self.assertRaises(ValueError):
            watch.updates(root, {'bun': '1.2.3'}, {'bun': item})
