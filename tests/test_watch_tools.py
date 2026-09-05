import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('watch', Path(__file__).parents[1]/'helpers/watch-tools.py')
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


class ToolWatchTests(unittest.TestCase):
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
