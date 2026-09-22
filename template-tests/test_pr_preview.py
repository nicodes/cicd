import re
from pathlib import Path
import unittest

WORKFLOW = Path(__file__).parents[1] / 'templates' / 'pr-preview.yml'
README = Path(__file__).parents[1] / 'templates' / 'README.md'

GUARD = 'github.event.pull_request.head.repo.full_name == github.repository'
MARKER = '<!-- preview -->'
COMPOSITE = 'nicodes/komizo-actions/preview'
# The peeled commit of the v0.0.19 release tag of komizo-actions — NOT the
# annotated tag object (3ebf1debe975a3f5f8d26f7f631181c46c6bd9ef).
V0_0_19_PEELED = 'c279f86a61d63fe929e018aa3b6c1077f3d8e65f'


def job(text, name):
    """The YAML section of one job, from its key to the next job key or EOF."""
    section = text.split(f'\n  {name}:\n', 1)
    assert len(section) == 2, f'exactly one {name} job'
    return re.split(r'\n  [a-z][\w-]*:\n', section[1], maxsplit=1)[0]


class PrPreviewTemplate(unittest.TestCase):
    """Structural pins for the PR preview template: the same-repo guard, the
    one-sticky-comment invariant, the fixed preview-composite contract and the
    permissions floor. Fixture-free and network-free like the rest of
    template-tests; behavior is pinned by exact expressions, not executed."""

    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text()
        cls.up = job(cls.text, 'preview-up')
        cls.down = job(cls.text, 'preview-down')

    def test_trigger_is_pull_request_with_exactly_the_four_types(self):
        self.assertIn('\non:\n  pull_request:\n', self.text)
        mentions = [line for line in self.text.splitlines() if 'pull_request_target' in line]
        self.assertTrue(mentions, 'the trigger choice is explained in a comment')
        for line in mentions:
            self.assertTrue(line.strip().startswith('#'),
                            'pull_request_target may only ever be mentioned in a comment: ' + line)
        self.assertEqual(self.text.count('types: [opened, synchronize, reopened, closed]'), 1,
                         'exactly opened, synchronize, reopened, closed — no more, no fewer')

    def test_same_repo_guard_is_job_level_on_every_privileged_job(self):
        self.assertEqual(self.text.count(GUARD), 2, 'one guard expression per job, nowhere else')
        for name, section in [('preview-up', self.up), ('preview-down', self.down)]:
            with self.subTest(job=name):
                head = section.split('runs-on:')[0]
                self.assertIn(GUARD, head, 'the guard sits in the job-level if, before any step')
                self.assertLess(head.index(GUARD), section.index('steps:'),
                                'job level — a step-level guard would still bring secrets into scope')

    def test_guard_splits_on_close(self):
        up_if = self.up.split('runs-on:')[0]
        down_if = self.down.split('runs-on:')[0]
        self.assertIn("github.event.action != 'closed'", up_if)
        self.assertIn("github.event.action == 'closed'", down_if)

    def test_permissions_floor_and_no_extra_secrets(self):
        self.assertIn('\npermissions:\n  contents: read\n', self.text,
                      'workflow-level floor is contents: read alone')
        for name, section in [('preview-up', self.up), ('preview-down', self.down)]:
            with self.subTest(job=name):
                self.assertIn('contents: read', section)
                self.assertIn('pull-requests: write', section, 'the sticky comment — nothing else')
        for scope in ['packages', 'id-token', 'deployments', 'secrets']:
            self.assertIsNone(re.search(rf'^\s*{scope}:', self.text, re.M),
                              f'{scope}: is never granted (comments explaining its absence excepted)')
        self.assertEqual(re.findall(r'secrets\.([A-Z_]+)', self.text),
                         ['KOMIZO_DEPLOY_KEY', 'KOMIZO_DEPLOY_KEY'],
                         'the only secret named, once per job, is the deploy key')
        self.assertEqual(sorted(set(re.findall(r'vars\.([A-Z_]+)', self.text))),
                         ['KOMIZO_KNOWN_HOSTS', 'KOMIZO_SERVER_URL'],
                         'the deploy composite\'s SSH env path, nothing else')

    def test_composite_is_pinned_to_the_v0_0_19_peeled_commit(self):
        uses = re.findall(rf'uses: {re.escape(COMPOSITE)}@([0-9a-f]{{40}})([^\n]*)', self.text)
        self.assertEqual(len(uses), 2, 'both jobs call the preview composite, SHA-pinned')
        for sha, comment in uses:
            self.assertEqual(sha, V0_0_19_PEELED,
                             'the pin is the v0.0.19 peeled commit, never the annotated tag object')
            self.assertIn('# v0.0.19', comment, 'the trailing comment names the release the SHA peels')
            self.assertNotIn('placeholder', comment, 'the placeholder note is gone — the pin is real')
        self.assertNotIn('3ebf1debe975a3f5f8d26f7f631181c46c6bd9ef', self.text,
                         'the annotated tag object SHA must never appear as the pin')

    def test_interface_contract_inputs_and_actions(self):
        for name, section, action in [('preview-up', self.up, 'up'), ('preview-down', self.down, 'down')]:
            with self.subTest(job=name):
                self.assertIn('app: ${{ env.APP }}', section)
                self.assertIn('pr-number: ${{ github.event.pull_request.number }}', section)
                self.assertIn('images: ${{ steps.images.outputs.refs }}', section)
                self.assertEqual(section.count(f'action: {action}'), 1)
                other = 'down' if action == 'up' else 'up'
                self.assertNotIn(f'action: {other}', section)
        self.assertEqual(self.text.count('ghcr.io/${{ github.repository_owner }}/'
                                         '$APP-$component:${{ github.event.pull_request.head.sha }}'), 2,
                         'image refs are ghcr.io/<owner>/<project>-<component>:<head-sha> in both jobs')

    def test_sticky_comment_find_or_create_then_update_in_place(self):
        self.assertGreaterEqual(self.up.count(MARKER), 2, 'the marker tags the body and keys the lookup')
        self.assertIn('--paginate', self.up, 'the marker lookup scans every comment page')
        self.assertIn('issues/$PR_NUMBER/comments', self.up)
        self.assertIn('--method POST', self.up, 'first deploy creates the one comment')
        self.assertIn('--method PATCH', self.up, 'synchronize updates the same comment in place')
        self.assertIn('issues/comments/$existing', self.up, 'the update targets the marker-found comment id')
        self.assertEqual(self.up.count('gh api'), 3, 'find, create and update — no other comment paths')
        for output in ['preview-url', 'api-url', 'gate-status']:
            self.assertIn(f'steps.preview.outputs.{output}', self.up,
                          f'the comment carries the composite\'s {output} output')
        self.assertIn('github.event.pull_request.head.sha', self.up, 'the comment names the deployed SHA')

    def test_close_marks_the_same_comment_dead_and_creates_nothing(self):
        self.assertIn(MARKER, self.down, 'the dead-mark keeps the marker so the comment stays identified')
        self.assertIn('torn down', self.down)
        self.assertIn('--method PATCH', self.down, 'dead-mark is an update of the marker-found comment')
        self.assertNotIn('--method POST', self.down, 'close never creates a comment')
        self.assertEqual(self.down.count('gh api'), 2, 'find and update only')

    def test_comment_uses_the_workflows_own_token_and_no_external_action(self):
        self.assertEqual(self.text.count('GH_TOKEN: ${{ github.token }}'), 2)
        for uses in re.findall(r'uses: ([^\s#]+)', self.text):
            action = uses.split('@', 1)[0]
            self.assertIn(action, ['actions/checkout', COMPOSITE],
                          f'{action}: the comment steps run the preinstalled gh CLI, not an action')
            self.assertRegex(uses.split('@', 1)[1], r'^[0-9a-f]{40}$',
                             f'{uses} must be pinned by full commit SHA')

    def test_concurrency_is_per_pr_and_teardown_is_never_cancelled(self):
        group = 'group: preview-${{ github.event.pull_request.number }}'
        self.assertIn(group, self.up)
        self.assertIn('cancel-in-progress: true', self.up, 'a new push supersedes the in-flight deploy')
        self.assertIn(group, self.down, 'same group: close queues behind an in-flight deploy')
        self.assertIn('cancel-in-progress: false', self.down, 'the close event is never superseded')

    def test_checkout_fetches_the_prs_own_head(self):
        self.assertIn('ref: ${{ github.event.pull_request.head.sha }}', self.up,
                      'pull_request, not pull_request_target: the preview deploys the PR\'s code')

    def test_readme_documents_adoption_and_the_invariants(self):
        readme = README.read_text()
        self.assertIn('## PR preview deployments', readme)
        self.assertIn(GUARD, readme, 'the guard\'s exact expression is documented as non-negotiable')
        self.assertIn('non-negotiable', readme)
        self.assertIn('pull_request_target', readme, 'the trigger choice is explained')
        self.assertIn(MARKER, readme, 'the one-sticky-comment invariant is documented')
        self.assertIn('KOMIZO_DEPLOY_KEY', readme)
        self.assertIn('KOMIZO_SERVER_URL', readme)
        self.assertIn('KOMIZO_KNOWN_HOSTS', readme)
        self.assertIn('fork', readme.lower(), 'the fork-PR skip behavior is documented')
        self.assertIn('cancel-in-progress: false', readme, 'the close-path interplay is documented')


if __name__ == '__main__':
    unittest.main()
