import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('update_bun', Path(__file__).parents[1]/'helpers/update-bun.py')
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


class BunUpdates(unittest.TestCase):
    def test_pinned_table_schema_and_out_of_range_updates(self):
        rows = update.parse_inventory('bun outdated v1.4.1 (4661e494f)\n|---|---|---|---|\n'
            '| Package | Current | Update | Latest |\n| @types/bun (dev) | 1.4.1 | 1.4.1 | 2.0.0 |\n')
        self.assertEqual(rows, [{'package': '@types/bun', 'current': '1.4.1', 'update': '1.4.1', 'latest': '2.0.0'}])
        for output in ['network unavailable', '| pkg | unknown | 1.0.0 | 2.0.0 |', '| pkg | 1.0.0 | 2.0.0 |']:
            with self.assertRaises(ValueError):
                update.parse_inventory(output)
        self.assertEqual(update.parse_inventory(''), [])

    def test_main_moved_blocks_all_writes(self):
        with patch.object(update, 'api', return_value={'object': {'sha': 'b'*40}}) as api:
            with self.assertRaises(ValueError):
                update.publish(Path('.'), 'nicodes/example-be', 'a'*40, [], {'app/bun.lock': 'changed'})
            self.assertEqual(api.call_count, 1)

    def test_existing_pr_preserves_owner_branch(self):
        def api(repo, suffix, payload=None):
            if suffix == 'git/ref/heads/main': return {'object': {'sha': 'a'*40}}
            if suffix.startswith('issues?'): return []
            if suffix.startswith('pulls?'): return [{'head': {'ref': 'automation/bun-refresh-existing', 'repo': {'full_name': repo}, 'sha': 'c'*40}}]
            if suffix.startswith('commits/'): return {'check_runs': [{'name': 'Test'}, {'name': 'Build'}]}
            raise AssertionError('Existing PR must not be overwritten')
        with patch.object(update, 'api', side_effect=api):
            update.publish(Path('.'), 'nicodes/example-be', 'a'*40, [], {'app/bun.lock': 'changed'})

    def test_only_expected_files_are_published_and_ci_is_explicit(self):
        calls = []
        def api(repo, suffix, payload=None):
            calls.append((suffix, payload))
            if suffix == 'git/ref/heads/main': return {'object': {'sha': 'a'*40}}
            if suffix.startswith(('issues?', 'pulls?', 'git/matching-refs/')): return []
            if suffix == 'git/commits/'+'a'*40: return {'tree': {'sha': 'b'*40}}
            if suffix in {'git/trees', 'git/commits'}: return {'sha': 'c'*40}
            if suffix == 'pulls': return {'number': 7}
            return None
        with patch.object(update, 'api', side_effect=api):
            update.publish(Path('.'), 'nicodes/example-be', 'a'*40, [], {'app/bun.lock': 'changed'})
        self.assertTrue(any(s == 'actions/workflows/ci.yml/dispatches' for s,p in calls))
        tree = next(p for s,p in calls if s == 'git/trees')
        self.assertEqual([i['path'] for i in tree['tree']], ['app/bun.lock'])
        self.assertFalse(any('merge' in s for s,p in calls))
