# Repository envelope templates

Owner: nicodes. Copy the selected archetype's `Makefile`, `.mise.toml` and
`.gitignore` into the product root and `ci.yml` into `.github/workflows/ci.yml`. Copy `dependabot.yml` into
`.github/dependabot.yml`, adding an explicit entry for every extra Go module.
These are envelope templates, not complete applications. Missing product scripts
fail the gate; never replace them with successful placeholders. The six migrated
repositories provide concrete, tested integrations.

`full-stack` uses `app/`, `api/`, `pb/`, `deploy/` and `docs/`; embedded and separate
PocketBase are both supported. `app-only` uses `app/`, `deploy/` and `docs/` and has
no API or database scaffold. Its Go tooling builds and scans Caddy, not a new
backend service. Extend exact tool pins when a product needs additional tools.

`static-web` is the static site envelope (Astro on bun in the current adopters,
hosted by Vercel, which deploys from its own build). Copy `ci.yml` into
`.github/workflows/ci.yml`, `dependabot.yml` into `.github/dependabot.yml`,
`.mise.toml`, `biome.json` and `.gitignore` into the root, and `actions/build/action.yml` and
`actions/test/action.yml` into `.github/actions/` — the workflow's jobs call
these repo-local composite actions, so they are part of the archetype, not
optional extras. This archetype omits the `Makefile` and `bun-updates.yml` on
purpose: none of the three adopting repositories has either. Their entire gate
is the two composite actions, and a second entry point would only drift from
it; the bun pin lives in `.mise.toml` and moves with the product.

## Fleet tool baseline

Every archetype pins its shared tools exactly, as `x.y.z`, in its
`.mise.toml`:

| Tool | Pin | Archetypes |
| --- | --- | --- |
| go | 1.27.0 | full-stack, app-only |
| golang.org/x/vuln/cmd/govulncheck | 1.7.0 | full-stack, app-only |
| bun | 1.4.1 | all three |
| node | 24.18.1 | full-stack, app-only |
| python | 3.13.11 | full-stack, app-only |

The baseline is per archetype, by need: `static-web` carries only bun because
its gate is the two composite actions and its CI runs no Go or Python.
Product-specific pins — Godot versions, extra node runtimes, komizo tooling —
are product choices and live in the product's own `.mise.toml`, out of scope
for this baseline.

The rule is exact `x.y.z` pins, so one version is in force everywhere a
repository runs. The single relaxation is mise's two-component core form
(`python = "3.12"`, which floats the patch release): the fleet watcher
normalizes it to a `x.y.0` comparison core rather than rejecting it, so it is
tolerated where a product already uses it, but new pins are always exact
three-component versions.

## Canonical lint and format: Biome

The web repositories share one lint+format configuration:
`static-web/biome.json` is the canonical artifact and is copied verbatim into
each adopting repository's root. The engine is `@biomejs/biome` at exactly
`2.5.14`, installed as a devDependency in the product's `package.json`
(the archetype ships no `package.json` of its own — the pin belongs to the
product's manifest and lockfile, under the usual frozen-install gate). Biome
is installed through the bun channel like every other JS dependency; the
archetype's Test gate then runs `bun x biome check`, which fails the build on
any lint or format violation against the canonical configuration. The
configuration's `$schema` URL records the same version, so the pin, the
schema and the gate move together in one commit when the fleet upgrades.

`ci.yml` and `actions/build/action.yml` are admitted copies, identical across
adopters. `actions/test/action.yml` is the per-repo override point: the
archetype ships the two common assertions (the build wrote `dist/index.html`;
no JavaScript was emitted) and the product adds its own assertions — routes
that must exist, content that must be present — as further steps in that file.
The push trigger on `ci.yml` is load-bearing here: there is no CD, and the
host's build never runs the assertions, so this workflow is the only thing
that gates a merge to main.

Vendor `helpers/` and `tests/` with the full source revision and SHA-256 inventory
in `scripts/engineering/SOURCE.json`, as described in the parent README. Keep the
normative policy in the September workspace plan. Use these product-owned scripts:

| Script | Required behavior |
| --- | --- |
| `scripts/test.sh` | Frozen Bun install, pin checks, Actionlint, ShellCheck, helper tests, Expo compatibility, TypeScript, lint, unit tests and product contracts. For every applicable Go module: formatting, vet and full integration/unit tests with `-race -count=1`; production-equivalent PocketBase fixtures. |
| `scripts/build.sh` | Exact-head web export and runtime image build; scan actual binaries and record the checked image archive with `release.py`. |
| `scripts/vuln.sh` | Online source dependency scans, fail closed on scanner failure and applicable vulnerabilities. |
| `scripts/e2e.sh` | Real Playwright core journey against built artifacts. Full-stack uses a signed isolated issuer, real API and disposable database, rejects fixture auth in production images, and exercises the real-image restore helper. |
| `scripts/dev-stack.sh` or `scripts/dev-app.sh` | Select nonconflicting ports, start the product processes in the owning supervisor and preserve local mutable state. Full-stack accepts API, DB and app preferred ports; app-only accepts the app port. |

Bare `make` is help. `make check` includes test, vulnerability checks, build and
browser E2E; build is an E2E prerequisite so direct `make e2e` is also complete.
`make stop` uses the checkout's authenticated supervisor, never port-based killing.
`make clean` removes generated output and preserves local databases.

CI jobs are exactly Test and Build and run independently. Test runs `make test
vuln`; Build runs `make install e2e`, including build and browser checks. Both jobs
are required before merge. Templates reference reviewed action SHAs, use exact
mise tools and contain no production secrets or deployment privileges.

App-only CI runs on main for deploy-ready applications such as Petalboard. When
adding reviewed CD, remove that redundant main trigger and rerun both gates on
the merged SHA in CD. Full-stack requires such product-owned CD before adoption.
Keep service/image lists, secret names, health URLs, backup exporters, production
synthetic identities, migration compatibility and deploy order in the product.
Use the checked release archive without rebuilding. Do not copy a generic
privileged deploy workflow. Petalboard remains unlaunched.

Concrete implementations: `nicodes/ormos-be`, `nicodes/cazper-be` and
`nicodes/komizo-be` for full-stack; `nicodes/ctcalc-be`, `nicodes/tonesplit-be` and
`nicodes/petalboard-be` for app-only. Their `SOURCE.json` records the exact shared
release; their integration tickets track final adoption and rollout evidence.
Static-web: `aviorstudio/aviorstudio-web`, `nicodes/tonesplit-web` and
`nicodes/ctcalc-web`.
