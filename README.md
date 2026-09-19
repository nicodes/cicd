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

<!-- ws2/reusable-vuln-tools: begin -->
## Shared reusable workflows: vulnerability scan and tool watch

`nicodes/cicd` is the single source of truth for the vulnerability-scan and
tool-watch workflow bodies that the product repositories used to carry as
near-identical copies. Product repositories keep thin callers that own only
their schedule and their project identity; the shared bodies live here.

### Caller contract: vulnerability scan (`vuln.yml`)

The caller keeps `on: schedule` with its own staggered cron minute plus
`workflow_dispatch`, and grants the workflow at least
`contents: read`, `packages: read`, `deployments: read`, `issues: write`
(a called workflow can only restrict, never elevate, permissions). The body —
checkout, mise, ghcr.io login, source scan, deployed-image scan and failure
reporting — comes from this repository:

```yaml
jobs:
  scan:
    uses: nicodes/cicd/.github/workflows/vuln.yml@<full-40-hex-cicd-commit>
    with:
      project: cazper
      source-scan-command: bun install --cwd app --frozen-lockfile && make vuln
```

`project` is required and is passed to the caller's own vendored
`scripts/engineering/helpers/scan-deployed.py`; that path stays the contract.
`source-scan-command` is the one optional knob: it runs in the caller's
checkout with the caller's mise toolchain active, and an empty value (the
default) skips the source-scan step. Failure reporting runs cicd's own
`helpers/report-failure.py` (checked out from the pinned cicd revision inside
the reusable workflow), so these two workflows no longer depend on the
caller's vendored `report-failure.py`; other workflows keep using it.

### Caller contract: tool watch (`tools.yml`)

The caller keeps `on: schedule` (weekly, own staggered minute) plus
`workflow_dispatch`, grants `contents: read` and `issues: write`, and calls:

```yaml
jobs:
  watch:
    uses: nicodes/cicd/.github/workflows/tools.yml@<full-40-hex-cicd-commit>
```

There are no inputs. The reusable job keeps the fleet's `main`-only guard
(`if: github.ref == 'refs/heads/main'`, evaluated in the caller's context),
the `tool-watch` concurrency group with `cancel-in-progress: false`, and the
`tool-updates` artifact (`.artifacts/tool-updates.json`, 30-day retention,
`if-no-files-found: ignore`, uploaded `if: always()`). It runs the caller's
vendored `scripts/engineering/helpers/watch-tools.py` with the caller's token.

### What this repository changed

- Added reusable `.github/workflows/vuln.yml` and `.github/workflows/tools.yml`.
- Internalized the `docker/login-action` pin the fleet duplicated.
- Renamed this repository's own scheduled maintenance workflow from
  `tools.yml` to `tool-maintenance.yml` (cron `31 9 * * 1`, `helpers/watch-tools.py`
  body and permissions unchanged) to free the name for the reusable workflow.

### Fleet divergence at adoption time

| Repository | vuln cron | `project` | Source scan | vuln shape replaced | tools copy |
|---|---|---|---|---|---|
| nicodes/cazper-be | `39 6 * * *` | `cazper` | `bun install --cwd app --frozen-lockfile && make vuln` | full copy, separate report job, 110 min | identical fleet copy |
| nicodes/ormos-be | `29 6 * * *` | `ormos` | `bun install --cwd app --frozen-lockfile && make vuln` | full copy, separate report job, 110 min | identical fleet copy |
| aviorstudio/termcade-be | `39 6 * * *` | `termcade` | `make vuln` | compact single-job copy, 90 min | identical fleet copy |
| aviorstudio/gdam-be | `39 6 * * *` | `gdam` | `make vuln` | compact single-job copy, 90 min | identical fleet copy |
| astrylogical/astry-be | `39 6 * * *` | `astry` | `make vuln` | compact single-job copy, 90 min | identical fleet copy |
| nicodes/komizo-be | `19 6 * * *` (daily) | `komizo` | guarded two-stage scan | none — bespoke drill/vuln-issue.sh report copy stays local | identical fleet copy |

All six `tools.yml` copies were byte-identical (weekly `41 9 * * 1`, same
steps and timeouts), so the reusable body reproduces them exactly; komizo-be's
vulnerability workflow keeps its local drill mode, gate-status verdict logic
and issue script and is intentionally not folded into the reusable body.
`nicodes/tonesplit-be` and `nicodes/ctcalc-be` carry an older weekly
`Dependency scan` without `scan-deployed.py`; their `tools.yml` copies match
the fleet shape and can adopt the reusable tool watch, but the vulnerability
reusable does not cover their shape.
<!-- ws2/reusable-vuln-tools: end -->

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
<!-- ws1: reusable-backup begin -->

## Reusable backup workflow

`.github/workflows/backup.yml` is the single source of truth for the nightly
encrypted-backup run. Product repositories replace their whole local
`.github/workflows/backup.yml` with a thin caller that keeps only the schedule,
the main-branch guard and the call itself:

```yaml
name: Backup
"on":
  schedule:
    - cron: 43 8 * * *   # keep each product's existing minute spread
  workflow_dispatch: null
permissions:
  contents: read
jobs:
  backup:
    name: Backup
    if: "github.ref == 'refs/heads/main'"
    uses: nicodes/cicd/.github/workflows/backup.yml@<full cicd commit SHA>
    with:
      product: komizo                  # slug naming the backup objects
      server: "${{ vars.SERVER_HOST }}" # or vars.KOMIZO_SERVER_URL
      user: komizo-cazper              # SSH deploy user
    secrets: inherit
```

The caller must keep its `production` environment, its own
`scripts/export-backup.sh` host-side exporter, and provide (with
`secrets: inherit`) the `KOMIZO_DEPLOY_KEY` secret, the `KOMIZO_KNOWN_HOSTS`,
`BACKUP_S3_HOSTNAME` and `BACKUP_S3_BUCKET` variables, and the
`BACKUP_S3_ACCESS_KEY` and `BACKUP_S3_SECRET_KEY` secrets. Vars and secrets
resolve in the caller's repository context. The reusable workflow checks the
caller out, connects through `komizo-actions/connect`, runs the exporter,
derives `taken_at` from the receipt's `verified_at`, PUTs the sealed pair with
the pinned cicd `helpers/upload-backup.py`, uploads the artifact for 30 days,
and reports failures through the pinned cicd `helpers/report-failure.py`. The
cicd helper source is itself checked out at a pinned full commit SHA, so no
runtime fetch of a moving ref is involved.

What the thin caller replaces, in full:

| Repository | Replaced by this call |
| --- | --- |
| nicodes/komizo-be `.github/workflows/backup.yml` | whole local Backup workflow |
| nicodes/cazper-be `.github/workflows/backup.yml` | whole local Backup workflow |
| aviorstudio/termcade-be `.github/workflows/backup.yml` | whole local Backup workflow |
| aviorstudio/gdam-be `.github/workflows/backup.yml` | whole local Backup workflow |

Re-land decision record, 2026-09-18: `helpers/upload-backup.py` and
`helpers/vendor-snapshot.py` are restored byte-identical from the reviewed
2057d819 snapshot. `upload-backup.py` never reached main (it lived on the
d5ef973 lineage); `vendor-snapshot.py` was dropped by revert 74f7c34. All four
product repositories vendor both files byte-identically
(`sha256 db56696bf8906daa3469e82c0be30b3ca50dff4057634ecfdc6e6ae32377e18a` and
`fcecb4b627cbbf8e91c3e48fa02e0b8e3329fde10e916a5a2e5d6c68cada1027`), so
re-vendoring from main would silently drop them and break each product's
`scripts/upload-backup.sh` at runtime. This record supersedes the earlier
"abandoned and removed" paragraph for these two helpers; their restored tests
run in `make check`. The four product `scripts/upload-backup.sh` wrappers are
byte-identical modulo the slug and the vendored helper path, so the reusable
workflow invokes the re-landed helper directly instead of the wrapper.

<!-- ws1: reusable-backup end -->
