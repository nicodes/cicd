import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('scan_deployed', Path(__file__).resolve().parents[1]/'helpers/scan-deployed.py')
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


class DeployedRevision(unittest.TestCase):
    def test_failed_latest_deploy_does_not_hide_prior_success(self):
        responses = [[{'id': 3, 'sha': 'a'*40}, {'id': 2, 'sha': 'b'*40}],
                     [{'state': 'failure'}, {'state': 'success'}], [{'state': 'success'}]]
        with patch.object(scanner, 'api', side_effect=responses):
            self.assertEqual(scanner.deployed_revisions('nicodes/ormos-be'), ['a'*40, 'b'*40])

    def test_missing_success_invalid_sha_or_protocol_fails(self):
        for responses in [[[]], [[{'id': 1, 'sha': 'main'}], [{'state': 'success'}]],
                          [[{'id': False}]], [[{'id': 1}], {}]]:
            with self.subTest(responses=responses), patch.object(scanner, 'api', side_effect=responses), self.assertRaises(ValueError):
                scanner.deployed_revisions('nicodes/ormos-be')

    def test_new_products_require_their_exact_repository_and_image_namespace(self):
        for project, owner in [('gdam', 'aviorstudio'), ('termcade', 'aviorstudio'), ('astry', 'astrylogical')]:
            refs = scanner.product_images(project, f'{owner}/{project}-be', 'a'*40)
            self.assertTrue(all(ref.startswith(f'ghcr.io/{owner}/{project}-') for ref in refs))
            for repository in [f'nicodes/{project}-be', f'{owner}/unrelated']:
                with self.assertRaises(ValueError):
                    scanner.product_images(project, repository, 'a'*40)
        self.assertTrue(any('astry-pb:' in ref for ref in scanner.product_images('astry', 'astrylogical/astry-be', 'a'*40)))

    def test_existing_products_keep_their_explicit_be_repositories(self):
        for project, owner in [('ormos', 'nicodes'), ('cazper', 'nicodes'), ('komizo', 'nicodes'),
                               ('gdam', 'aviorstudio'), ('termcade', 'aviorstudio'), ('astry', 'astrylogical')]:
            with self.subTest(project=project):
                self.assertEqual(scanner.PRODUCTS[project][:2], (owner, f'{project}-be'))
                with self.assertRaises(ValueError):
                    scanner.product_images(project, f'{owner}/{project}', 'a'*40)
        self.assertEqual(scanner.product_images('komizo', 'nicodes/komizo-be', 'b'*40),
                         [f'ghcr.io/nicodes/komizo-service:{"b"*40}',
                          f'ghcr.io/nicodes/komizo-gate:{"b"*40}'])

    def test_fieldsofrevik_scans_its_real_repository_without_the_be_suffix(self):
        refs = scanner.product_images('fieldsofrevik', 'aviorstudio/fieldsofrevik', 'a'*40)
        self.assertEqual(refs, [f'ghcr.io/aviorstudio/fieldsofrevik-{component}:{"a"*40}'
                                for component in ['api', 'godot-api', 'db', 'gate']])
        for repository in ['aviorstudio/fieldsofrevik-be', 'nicodes/fieldsofrevik', 'aviorstudio/revik-be']:
            with self.subTest(repository=repository), self.assertRaises(ValueError):
                scanner.product_images('fieldsofrevik', repository, 'a'*40)
        with self.assertRaises(ValueError):
            scanner.product_images('fieldsofrevik', 'aviorstudio/fieldsofrevik', 'main')

    def test_unknown_product_is_rejected_by_the_allowlist(self):
        with self.assertRaises(KeyError):
            scanner.product_images('unknown', 'nicodes/unknown-be', 'a'*40)


if __name__ == '__main__':
    unittest.main()
