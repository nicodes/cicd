"""The auth boundary gate: three environments, three identity sources.

Two of these tests exist because the first version of the gate got the
answer backwards in both directions -- it failed the product that implements
the contract best, and passed the ones that implement none of it. Both are
pinned here so neither can come back.
"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from vendored import skip_module_if_vendored

skip_module_if_vendored("cicd-only: FLEET.json and the fleet's own auth contract")

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'helpers/auth-boundary.mjs'

CLERK_API = {'provider': 'clerk', 'api': True, 'enforced': True}
CLERK_CLIENT = {'provider': 'clerk', 'api': False, 'enforced': True}
NO_PROVIDER = {'provider': 'none', 'api': False, 'enforced': True}

TAGGED_BYPASS = '''//go:build localdev

package clerkauth

import "os"

func local() string {
\tif os.Getenv("LOCAL_AUTH") == "1" {
\t\treturn devClerkID
\t}
\treturn ""
}

const devClerkID = "dev_local_user"
'''

REFUSAL = '''//go:build !localdev

package config

import (
\t"fmt"
\t"os"
)

func configureLocalAuth() error {
\tif os.Getenv("LOCAL_AUTH") != "" {
\t\treturn fmt.Errorf("local authentication requires the separately compiled localdev API")
\t}
\treturn nil
}
'''


def check(root, declared):
    program = (
        f'const m = await import({json.dumps(str(HELPER))});'
        'const [root, declared] = JSON.parse(process.argv[1]);'
        'console.log(JSON.stringify(m.checkAuthBoundary(root, declared)));'
    )
    result = subprocess.run(['bun', '--eval', program, json.dumps([str(root), declared])],
                            capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def failures(findings):
    return [f['text'] for f in findings if f['level'] == 'fail']


class Product:
    """A throwaway product tree."""

    def __init__(self, directory):
        self.root = Path(directory)

    def go(self, relative, body):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        return self

    def workflow(self, name, body):
        path = self.root / '.github/workflows' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        return self

    def web(self, relative, body):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        return self

    def compliant(self):
        """The shape termcade and gdam have between them."""
        self.go('api/internal/clerkauth/local_auth.go', TAGGED_BYPASS)
        self.go('api/internal/config/local_auth_disabled.go', REFUSAL)
        self.workflow('pr-preview.yml',
                      'jobs:\n  p:\n    steps:\n      - env:\n'
                      '          CLERK_SECRET_KEY_DEV: x\n'
                      '          CLERK_AUTHORIZED_PARTIES: https://pr-1.preview.example\n')
        self.workflow('cd.yml', 'jobs:\n  d:\n    steps:\n      - env:\n          CLERK_SECRET_KEY_PROD: y\n')
        return self


class CompliantProduct(unittest.TestCase):
    def test_a_product_with_the_whole_contract_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            Product(directory).compliant()
            self.assertEqual(failures(check(directory, CLERK_API)), [])


class LocalBypass(unittest.TestCase):
    def test_a_runtime_flag_is_refused_because_it_ships(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Product(directory).compliant()
            product.go('api/cmd/api/auth.go',
                       'package main\n\nimport "os"\n\nconst devClerkID = "dev_local_user"\n\n'
                       'func auth() string {\n\tif os.Getenv("APP_DEV") == "1" {\n'
                       '\t\treturn devClerkID\n\t}\n\treturn ""\n}\n')
            said = ' '.join(failures(check(directory, CLERK_API)))
            self.assertIn('api/cmd/api/auth.go', said)
            self.assertIn('go:build localdev', said)

    def test_having_no_local_path_at_all_is_a_failure_not_a_pass(self):
        """The first version passed this, which is backwards: no bypass means
        a developer points a laptop at a real Clerk instance."""
        with tempfile.TemporaryDirectory() as directory:
            product = Product(directory).compliant()
            (product.root / 'api/internal/clerkauth/local_auth.go').unlink()
            (product.root / 'api/internal/config/local_auth_disabled.go').unlink()
            said = ' '.join(failures(check(directory, CLERK_API)))
            self.assertIn('no local development identity exists', said)

    def test_a_bypass_with_no_refusal_counterpart_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Product(directory).compliant()
            (product.root / 'api/internal/config/local_auth_disabled.go').unlink()
            said = ' '.join(failures(check(directory, CLERK_API)))
            self.assertIn('!localdev', said)

    def test_a_test_file_does_not_count_as_a_bypass(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Product(directory).compliant()
            product.go('api/internal/clerkauth/other_test.go',
                       'package clerkauth\n\nconst devClerkID = "dev_local_user"\n'
                       '// LOCAL_AUTH in a test\n')
            self.assertEqual(failures(check(directory, CLERK_API)), [])


class PublicVariables(unittest.TestCase):
    def test_a_client_only_public_variable_is_not_a_finding(self):
        """termcade's EXPO_PUBLIC_LOCAL_AUTH_URL tells the app which local
        issuer to use. No server trusts it, so it grants nothing -- and the
        first version of this gate failed the best product in the fleet for
        having it."""
        with tempfile.TemporaryDirectory() as directory:
            product = Product(directory).compliant()
            product.web('app/src/local-auth.ts',
                        'export const url = process.env.EXPO_PUBLIC_LOCAL_AUTH_URL;\n')
            self.assertEqual(failures(check(directory, CLERK_API)), [])

    def test_a_server_that_reads_one_is_a_finding(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Product(directory).compliant()
            product.go('api/internal/config/trust.go',
                       'package config\n\nimport "os"\n\n'
                       'func issuer() string { return os.Getenv("EXPO_PUBLIC_LOCAL_AUTH_URL") }\n')
            said = ' '.join(failures(check(directory, CLERK_API)))
            self.assertIn('browser-readable', said)
            self.assertIn('api/internal/config/trust.go', said)


class KeyRouting(unittest.TestCase):
    def test_one_workflow_reaching_both_instances_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Product(directory).compliant()
            product.workflow('both.yml',
                             'jobs:\n  x:\n    steps:\n      - env:\n'
                             '          CLERK_SECRET_KEY_DEV: a\n'
                             '          CLERK_SECRET_KEY_PROD: b\n')
            said = ' '.join(failures(check(directory, CLERK_API)))
            self.assertIn('both.yml', said)

    def test_an_unsuffixed_secret_names_no_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Product(directory)
            product.go('api/internal/clerkauth/local_auth.go', TAGGED_BYPASS)
            product.go('api/internal/config/local_auth_disabled.go', REFUSAL)
            product.workflow('cd.yml', 'jobs:\n  d:\n    steps:\n      - env:\n          CLERK_SECRET_KEY: y\n')
            said = ' '.join(failures(check(directory, CLERK_API)))
            self.assertIn('which instance', said)


class PreviewHalf(unittest.TestCase):
    def test_a_preview_without_the_development_instance_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Product(directory).compliant()
            product.workflow('pr-preview.yml',
                             'jobs:\n  p:\n    steps:\n      - env:\n'
                             '          CLERK_SECRET_KEY_PROD: x\n'
                             '          CLERK_AUTHORIZED_PARTIES: https://pr-1.preview.example\n')
            said = ' '.join(failures(check(directory, CLERK_API)))
            self.assertIn('development instance', said)

    def test_a_preview_that_names_no_origin_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Product(directory).compliant()
            product.workflow('pr-preview.yml',
                             'jobs:\n  p:\n    steps:\n      - env:\n          CLERK_SECRET_KEY_DEV: x\n')
            said = ' '.join(failures(check(directory, CLERK_API)))
            self.assertIn('CLERK_AUTHORIZED_PARTIES', said)

    def test_no_preview_workflow_is_reported_but_does_not_fail(self):
        """The preview half cannot apply to a product that has no previews."""
        with tempfile.TemporaryDirectory() as directory:
            product = Product(directory).compliant()
            (product.root / '.github/workflows/pr-preview.yml').unlink()
            findings = check(directory, CLERK_API)
            self.assertEqual(failures(findings), [])
            self.assertTrue(any('no preview workflow' in f['text'] for f in findings))


class DeclaredProvider(unittest.TestCase):
    def test_a_product_declaring_none_may_not_reference_clerk(self):
        with tempfile.TemporaryDirectory() as directory:
            Product(directory).web('app/src/session.ts', 'import { useClerk } from "@clerk/clerk-react";\n')
            said = ' '.join(failures(check(directory, NO_PROVIDER)))
            self.assertIn('declared auth.provider "none"', said)

    def test_a_product_declaring_none_and_using_none_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            Product(directory).web('app/src/main.ts', 'export const x = 1;\n')
            self.assertEqual(failures(check(directory, NO_PROVIDER)), [])

    def test_a_client_only_product_is_asked_for_no_server_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            Product(directory).web('app/src/session.ts', 'import { useClerk } from "@clerk/clerk-react";\n')
            self.assertEqual(failures(check(directory, CLERK_CLIENT)), [])

    def test_an_undeclared_product_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            said = ' '.join(failures(check(directory, None)))
            self.assertIn('no auth declaration in FLEET.json', said)


class FleetDeclarations(unittest.TestCase):
    def test_every_product_declares_its_auth_posture(self):
        fleet = json.loads((ROOT / 'FLEET.json').read_text())
        for repo, entry in fleet['products'].items():
            with self.subTest(product=repo):
                self.assertIn('auth', entry, f'{repo} has no auth declaration')
                for field in ('provider', 'api', 'enforced'):
                    self.assertIn(field, entry['auth'])
                self.assertIn(entry['auth']['provider'], ('clerk', 'none'))

    def test_the_contract_names_all_three_environments(self):
        fleet = json.loads((ROOT / 'FLEET.json').read_text())
        self.assertEqual(sorted(fleet['auth_contract']['environments']),
                         ['local', 'preview', 'production'])


if __name__ == '__main__':
    unittest.main()


class RecognisesStructureNotSpelling(unittest.TestCase):
    """The rule is about the build tag, not about a magic string.

    The gate failed gdam's genuine local path because gdam writes
    "sk_test_local_development_only" with underscores where termcade uses a
    hyphen. A product should not have to spell a constant a particular way
    to satisfy a structural rule.
    """

    def test_a_tagged_bypass_counts_however_it_names_its_key(self):
        with tempfile.TemporaryDirectory() as directory:
            product = Product(directory).compliant()
            (product.root / 'api/internal/clerkauth/local_auth.go').write_text(
                '//go:build localdev\n\npackage clerkauth\n\nimport "os"\n\n'
                'func local() string {\n\tif os.Getenv("GDAM_LOCAL_AUTH") != "1" {\n'
                '\t\treturn ""\n\t}\n\treturn "sk_test_local_development_only"\n}\n')
            self.assertEqual(failures(check(directory, CLERK_API)), [])

    def test_an_untagged_grant_still_fails_even_beside_a_tagged_one(self):
        """Having a proper local path does not excuse a second, shipped one."""
        with tempfile.TemporaryDirectory() as directory:
            product = Product(directory).compliant()
            product.go('api/cmd/api/auth.go',
                       'package main\n\nimport "os"\n\nconst devClerkID = "dev_local_user"\n\n'
                       'func auth() string {\n\tif os.Getenv("APP_DEV") == "1" {\n'
                       '\t\treturn devClerkID\n\t}\n\treturn ""\n}\n')
            said = ' '.join(failures(check(directory, CLERK_API)))
            self.assertIn('api/cmd/api/auth.go', said)

    def test_the_refusal_file_is_not_mistaken_for_the_bypass(self):
        """`//go:build !localdev` contains the word; it is the opposite file."""
        with tempfile.TemporaryDirectory() as directory:
            product = Product(directory).compliant()
            (product.root / 'api/internal/clerkauth/local_auth.go').unlink()
            said = ' '.join(failures(check(directory, CLERK_API)))
            self.assertIn('no local development identity exists', said)
