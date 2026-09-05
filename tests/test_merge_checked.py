import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
spec = importlib.util.spec_from_file_location('merge', Path(__file__).resolve().parents[1]/'helpers/merge-checked.py')
merge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(merge)

class MergeEvidence(unittest.TestCase):
    def setUp(self):
        self.head = 'a'*40
        self.prefix = 'https://github.com/nicodes/example/actions/runs/123/'
        self.checks = [{'name': name, 'head_sha': self.head, 'status': 'completed', 'conclusion': 'success',
                        'app': {'slug': 'github-actions'}} for name in ['Test', 'Build']]
    def decide(self, checks, statuses=()):
        return merge.decision(checks, statuses, self.head, self.prefix)
    def test_missing_pending_skipped_failed_and_wrong_head(self):
        self.assertTrue(self.decide(self.checks))
        self.assertFalse(self.decide(self.checks[:1]))
        for update, raises in [({'status':'in_progress','conclusion':None}, False),
                               ({'conclusion':'skipped'}, True), ({'conclusion':'failure'}, True),
                               ({'head_sha':'b'*40}, True), ({'conclusion':'neutral'}, True)]:
            checks = copy.deepcopy(self.checks); checks[1].update(update)
            if raises:
                with self.assertRaises(ValueError): self.decide(checks)
            else: self.assertFalse(self.decide(checks))
    def test_only_own_running_check_is_excluded(self):
        check = {'name':'auto-merge','head_sha':self.head,'status':'in_progress', 'conclusion':None,
                 'details_url':self.prefix+'job/4'}
        self.assertTrue(self.decide(self.checks+[check]))
        check['details_url']='https://github.com/nicodes/example/actions/runs/999/job/4'
        self.assertFalse(self.decide(self.checks+[check]))
    def test_external_status_and_check_cannot_be_hidden(self):
        with self.assertRaises(ValueError):
            self.decide(self.checks, [{'context':'external','state':'failure'}])
        self.assertFalse(self.decide(self.checks, [{'context':'external','state':'pending'}]))
        checks = copy.deepcopy(self.checks); checks[1]['app']['slug']='other-app'
        self.assertFalse(self.decide(checks))
        checks = self.checks+[{'name':'extra','head_sha':self.head,'status':'completed','conclusion':'cancelled'}]
        with self.assertRaises(ValueError): self.decide(checks)

    def test_check_pagination_and_status_query(self):
        with patch.object(merge, 'api', side_effect=[{'check_runs': [1]*100}, {'check_runs': [2]}]) as api:
            self.assertEqual(len(merge.pages('checks?filter=latest', 'check_runs')), 101)
            self.assertEqual(api.call_args.args[0], 'checks?filter=latest&per_page=100&page=2')
        with patch.object(merge, 'api', return_value=[]) as api:
            self.assertEqual(merge.pages('statuses'), [])
            self.assertEqual(api.call_args.args[0], 'statuses?per_page=100&page=1')
