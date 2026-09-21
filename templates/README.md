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
`.mise.toml`, `.gitignore` and `biome.json` into the root, and
`actions/build/action.yml` and `actions/test/action.yml` into `.github/actions/` — the workflow's jobs call
these repo-local composite actions, so they are part of the archetype, not
optional extras. This archetype omits the `Makefile` and `bun-updates.yml` on
purpose: none of the three adopting repositories has either. Their entire gate
is the two composite actions, and a second entry point would only drift from
it; the bun pin lives in `.mise.toml` and moves with the product.

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

## Fleet tool baseline

The shared-tool baseline, pinned at exact versions across every archetype's
`.mise.toml`: **go 1.27.0, govulncheck 1.7.0, bun 1.4.1, python 3.13.11**.
These are the tools the fleet's shared gates run on; a product needing an
additional tool extends the pins, but it does not float one of these.
godot, node and komizo-tool pins are product choices and stay out of this
baseline. The exact-version rule (`x.y.z`) stands; the one accepted
relaxation is a two-component core (`python = "3.12"`), which floats the
patch and compares as `3.12.0` (the watcher's normalization).

`static-web` additionally pins Biome (`aqua:biomejs/biome`, currently 2.5.14)
in its `.mise.toml` and ships `biome.json` — the canonical lint+format
config the five web repositories adopt verbatim; its CI gate runs
`biome ci` against it. None of the web repos invents its own Biome config.
