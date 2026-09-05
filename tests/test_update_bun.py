import importlib.util
import json
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
                update.publish('nicodes/example-be', 'a'*40, [], {'app/bun.lock': 'changed'})
            self.assertEqual(api.call_count, 1)

    def test_transitive_refresh_creates_only_an_owned_issue(self):
        calls = []
        def api(repo, suffix, payload=None):
            calls.append((suffix, payload))
            if suffix == 'git/ref/heads/main': return {'object': {'sha': 'a'*40}}
            if suffix == 'issues': return {'number': 7}
            raise AssertionError('Only issue creation is permitted')
        with patch.object(update, 'api', side_effect=api), patch.object(update, 'run', return_value='[]') as run:
            update.publish('nicodes/example-be', 'a'*40, [], {'app/bun.lock': 'changed'})
        self.assertEqual(run.call_args.args[0][:3], ['gh', 'issue', 'list'])
        body = calls[-1][1]
        self.assertEqual(body['assignees'], ['nicodes'])
        self.assertIn('app/bun.lock', body['body'])
        self.assertIn('open a PR manually', body['body'])

    def test_existing_issue_is_updated_without_pr_or_code_writes(self):
        def run(args, **kwargs):
            if args[:3] == ['gh', 'issue', 'list']:
                return json.dumps([{'number': 4, 'title': 'Bun maintenance: available dependency updates'}])
            self.assertEqual(args[:3], ['gh', 'api', 'repos/nicodes/example-be/issues/4'])
            self.assertIn('PATCH', args)
            self.assertEqual(json.loads(kwargs['input'])['assignees'], ['nicodes'])
            return '{}'
        with patch.object(update, 'api', return_value={'object': {'sha': 'a'*40}}) as api, patch.object(update, 'run', side_effect=run):
            update.publish('nicodes/example-be', 'a'*40, [], {'app/bun.lock': 'changed'})
            self.assertEqual(api.call_count, 1)

    def test_no_updates_does_not_close_or_create_issues(self):
        with patch.object(update, 'api', return_value={'object': {'sha': 'a'*40}}) as api, patch.object(update, 'run') as run:
            update.publish('nicodes/example-be', 'a'*40, [], {})
            self.assertEqual(api.call_count, 1)
            run.assert_not_called()
