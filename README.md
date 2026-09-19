# Shared engineering helpers

Portable checks used by the six Nicodes products. Product repositories keep
their Make targets, release images, public origins, secrets, database handling,
and deployment order explicit. Helpers neither publish nor deploy implicitly.

The experimental PostgreSQL/vendor recovery additions from the uninterrupted
rollout attempt were abandoned and removed. The established authenticated
SQLite snapshot, restore-drill, release, and dependency controls below remain
the supported shared helpers.

`make install` installs exact tools; `make check` exercises security repair,
export failure, and checkout process ownership boundaries and validates syntax.

Products vendor `helpers/` and `tests/` beneath `scripts/engineering/`, with a
`SOURCE.json` containing this repository's full commit SHA and every copied
file's SHA256. Update the complete snapshot together and run the product's full
`make check`. No runtime network fetch of helper code is needed.

- `dev.py`: live supervisor and authenticated local stop socket; no port-based
  killing and no persisted PID used as shutdown authority.
- `export-web.py`: clean, bounded Expo export; requires exit0, HTML and JS.
- `pins.mjs`: exact tool/module/lockfile/image/action references and dependency-update coverage.
- `dependency-coverage.mjs`: require scheduled updates for the app, actions, images
  and every tracked Go module; a newly added module cannot silently lose coverage.
- `audit.py`: live online dependency audit; any local repair needs reviewed
  patch and installed-source hashes, adversarial tests, an owner and review date.
- `dependency-compatibility.cjs`: exercise repaired transitive dependency calls.

The normative standard remains the September4 workspace standardization plan.
Shared rollout work is tracked in nicodes/komizo-be#215.

`helpers/release.py` records the release archive hash and exact image IDs after
Build, then verifies the project, full commit, components, archive and loaded
images before publication. Publish loads those artifacts and never rebuilds.

Dependency automation uses `dependency-policy.py` before `merge-checked.py`.
The policy requires review for sensitive libraries, grouped changes, majors,
pre-1.0 minor updates and unknown version formats. The merge gate requires
successful Test and Build checks on the current head, checks all other reported
checks and statuses, rejects skipped required checks, and uses GitHub's atomic
head-SHA merge condition. Only trusted base code runs with merge credentials.
`report-failure.py` maintains an owned GitHub issue linked to bounded run evidence.

Recovery helpers verify safe archives and SQLite integrity (including WAL), bind
image metadata inside authenticated encryption, and validate off-host transport.
`restore-drill.py` boots the recorded database, API and frontend images against
a disposable clone with no external container network. Its optional `--pull`
mode authenticates the backup's project/revision before reading registry images.
Recovery and database settings keys must be supplied explicitly; the helper
never extracts credentials from a production container.

By owner decision on 2026-09-05, automated product restores are disabled.
Recovery private keys stay on the operator computer, outside GitHub and
production hosts. Initial migration restore verification and manual recovery
remain supported. Production hosts export encrypted snapshots using only
the public certificate. The restore preflight requires 768 MiB available memory;
the current 1 GB production hosts do not meet it alongside their live services.
`test-restore-drill.py` exercises the same command with disposable local data and
keys. Production data or credentials are not needed for the fixture.

`scan-image.py` scans actual runtime Go binaries; `scan-deployed.py` covers both
the latest deployment attempt and the last successful revision. Caddy is built
from the locked helper module with upstream race tests and a live binary scan.
The weekly central tool watcher includes its full module inventory. Stateful
rollback is denied unless `rollback-decision.py` receives the exact same-run
old/new read-write compatibility proof required by the product policy.

[Archetype templates](templates/README.md) provide the shared Make, CI, exact-tool
and ignore-file envelope. Product scripts keep runtime and deployment behavior
explicit; template regression tests exercise complete gates and failure propagation.

## Bun update compatibility and issue-only decision

GitHub Dependabot rejects the Bun 1.4.1 lockfile format (version 3). Use each
archetype's `bun-updates.yml` as `.github/workflows/bun-updates.yml`; Dependabot
continues covering actions, images and every Go module.

Owner decision, 2026-09-05: the weekly native Bun workflow creates or updates one
issue assigned to nicodes. It reports available direct releases and whether a
within-range/transitive refresh is available, using only a temporary copy with
lifecycle scripts disabled. It never publishes code, creates PRs, approves or
merges changes, or dispatches CI. Its job has only `contents: read` and
`issues: write`; no production environment, new app or personal token is needed.
Open update PRs manually and run the full Test/Build gate before merging.

Do not enable or broaden GitHub's combined workflow PR creation/approval setting
for this watcher. Existing repository settings are unchanged; explicit job
permissions apply even where a repository has broader defaults. Owner: nicodes. Review at the 2026-10-05 maintenance review, or when Dependabot supports
the lockfile format. Any move back to automatic Bun PR creation requires a new
owner decision; this review date does not automatically enable anything.

## Reusable Dependabot auto-merge gate

`.github/workflows/dependabot.yml` in this repository is the shared reusable
form of the product Dependabot auto-merge gate. A called workflow cannot own
event triggers, so every product keeps a thin caller file — only the trigger,
concurrency and permission envelope plus one job calling the pinned workflow:

```yaml
name: Dependabot
on:
  pull_request_target:
    types: [opened, synchronize, reopened, ready_for_review]
permissions:
  contents: write
  pull-requests: write
  actions: write
  checks: read
  statuses: read
concurrency:
  group: dependabot-${{ github.event.pull_request.number }}
  cancel-in-progress: true
jobs:
  auto-merge:
    uses: nicodes/cicd/.github/workflows/dependabot.yml@<full SHA of the reviewed cicd revision>
```

Inside the called workflow the `github` context, `github.token` and the caller's
event payload all resolve in the caller's repository, so the gate steps are the
per-repo contract unchanged. `GITHUB_TOKEN` is implicit: a called workflow is
automatically granted `github.token` and `secrets.GITHUB_TOKEN`, so the caller
job needs no `secrets:` block. The caller must keep the `permissions:` block
above, though. A called workflow can only downgrade the caller's token
permissions, never elevate them, and a caller that omits the block falls back
to the repository's default (read-only in most repos), which makes the merge
step fail. Keep the `concurrency:` block in the caller too: cancelling older
runs for the same pull request belongs to the file that owns the trigger.

Callers still vendor `dependency-policy.py` and `merge-checked.py` under
`scripts/engineering/helpers/` with their `SOURCE.json`. The gate checks out
and runs the caller's protected base at the pull request's base SHA, so the
policy of record stays the caller's reviewed base rather than this repository.

Security properties preserved from the per-repo form:

1. Policy scripts run from the caller's checkout at
   `github.event.pull_request.base.sha` — the trusted protected base, never the
   pull request head. The explicit `ref:` is load-bearing in the reusable form:
   without it a checkout in a called workflow follows the pull request's merge
   ref.
2. The caller resolves this workflow file by full commit SHA, so a
   `pull_request_target` pull request cannot redirect the gate logic.
3. The merge step only runs when the caller's `dependency-policy.py` set
   `eligible=true`, and `merge-checked.py` pins, verifies and merges the exact
   reviewed head SHA after every check passed.

Fleet status: `nicodes/cazper-be`, `nicodes/ctcalc-be`, `nicodes/komizo-be`,
`nicodes/ormos-be`, `nicodes/tonesplit-be`, `aviorstudio/gdam-be`,
`aviorstudio/termcade-be` and `astrylogical/astry-be` carry the per-repo
contract form and replace their copied steps with the thin caller first.
Weaker variants are Phase-2 candidates, deliberately untouched here. Twenty-one
repositories auto-merge minor and patch updates with `gh pr merge --auto`
gated only on the update type — without the sensitive-dependency policy,
trusted-base checkout or exact-head merge gate: `cazper-web`, `ctcalc-web`,
`komizo`, `komizo-actions`, `komizo-web`, `nicodes-web`, `ormos`, `ormos-web`,
`tonesplit-web`, `aviorstudio-web`, `fieldsofrevik-web`, `gdam`,
`gdam-actions`, `gdam-web`, `gdlint`, `gdlint-web`, `komizo-127`,
`komizo-actions-127`, `termcade`, `termcade-web` and `termcade-games`.
`aviorstudio/castledrop`, `aviorstudio/fieldsofrevik` and `aviorstudio/prizm`
inline a direct `gh pr merge --squash` dependabot job in their `ci.yml`.
