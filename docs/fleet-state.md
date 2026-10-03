# Fleet state

What the nine products share, how the foundations fit together, and the
constraints that are not visible from any single repository. Written
2026-10-03.

`FLEET.json` is the machine-readable half of this and wins where the two
disagree; this file explains the parts a JSON file cannot.

## The products

Nine products, three owners, two hosts.

| product | owner | host | profile |
| --- | --- | --- | --- |
| `nicodes/cazper-be` | nicodes | komizo.ni.codes | service |
| `nicodes/ctcalc-be` | nicodes | komizo.ni.codes | service |
| `nicodes/tonesplit-be` | nicodes | komizo.ni.codes | service |
| `astrylogical/astry-be` | astrylogical | komizo.ni.codes | service |
| `aviorstudio/gdam-be` | aviorstudio | komizo.avior.studio | godot |
| `aviorstudio/termcade-be` | aviorstudio | komizo.avior.studio | service |
| `aviorstudio/fieldsofrevik` | aviorstudio | komizo.avior.studio | godot |
| `aviorstudio/castledrop` | aviorstudio | komizo.avior.studio | godot |
| `aviorstudio/prizm` | aviorstudio | komizo.avior.studio | godot |

`nicodes` is a **personal account, not an organization**. It has no
organization secrets or variables, and it never will. Anything the three
products under it share is repeated at repository level by necessity.

Hostnames are published by each app's own route file and are not derivable
from the product name. Read them from `/srv/_proxy/routes/<app>.caddy` on the
host rather than guessing: `app.revik.gg`, `app.gdam.dev`,
`castledrop.avior.studio`, `app.cazper.ai`, `astry.app` and so on do not
follow one pattern.

---

## komizo

`komizo` is the deployment system: a CLI run from an operator's machine
against a server over SSH, plus the root-owned scripts it installs there. It
is not a service and there is nothing to log in to.

### What lives on a host

```
/usr/local/bin/deploy-<app>              generated per app, root-owned, doas-invokable
/usr/local/bin/set-secret-<app>          writes one key into the app's secrets.env
/usr/local/bin/komizo-preview            preview up/down/ls/gc
/usr/local/bin/write-preview-stackenv    the only permitted write under previews/
/usr/local/bin/komizo-box                the on-box agent
/var/lib/komizo/apps/<app>.env           app record: name, dir, CI user, tasks, flags
/var/lib/komizo/previews/<app>-pr-<N>/   per-preview state, 0750 root
/srv/<app>/                              compose.yml, secrets.env, the app's state
/srv/_proxy/routes/<app>.caddy           the hostnames this app claims
```

Each product gets its own `komizo-<app>` account on the host, and `doas` rules
naming the exact commands that account may run as root. A leaked deploy key
reaches one app's deploy script and nothing else.

### Setting a server up

`komizo init --host root@<server>` installs Docker, the shared `edge` network,
the firewall hook, the one Caddy reverse proxy that terminates TLS for every
app, and the nightly image reclaim. It is safe to re-run and is the only way
to change what the host has — there is no configuration management beyond it.

`komizo add` creates an app: the account, the doas rules, the directories, the
generated deploy script, and optionally the preview helpers (`--preview`).

### Deploying

CI calls the `deploy` composite, which SSHes as `komizo-<app>` and runs
`doas /usr/local/bin/deploy-<app> <version> ...`. The script pulls images
tagged by commit, rewrites `compose.yml`, brings the stack up, writes the
route file and reloads the shared proxy.

**Capacity floors.** Before anything else the script refuses to deploy when
free disk or memory is under the floor in `/etc/komizo/floors`, and warns
between one and two times it. The refusal is the whole point: it fails before
the disk fills rather than during. Its messages are a closed set, all of the
form `deploy: refusing: <reason>` with only paths and byte counts
interpolated.

**Image reclaim.** Images are tagged by commit, so nothing a deploy replaces
is ever *dangling* and a bare `docker image prune` collects none of it. The
reclaim is therefore `docker image prune -af` with no age filter: keep what a
container references, drop the rest. A stopped container still counts as a
reference, so an app somebody stopped keeps the image it will restart with,
and a rollback never needs a local image anyway — it is an ordinary deploy of
an older tag, which pulls.

This is **server-level and deliberately unreachable from any deploy key**: a
machine-wide prune has no business in a per-app path. It runs from
`/etc/periodic/daily/komizo-reclaim` via busybox `crond`, installed by
`komizo init`, which also enables `crond` because Alpine does not start it on
a minimal install. The same job runs `komizo-box preview gc` first, so a
preview released tonight has its image collected tonight.

> The reclaim existed for a while as an init-time step only, which meant it
> ran the day a box was built and never again. One host drifted to 226 images
> and 92% full, crossed the deploy floor, and then *every* app on it refused
> to deploy with no way to recover but an operator at an SSH prompt. A floor
> with nothing holding the box above it turns a slow problem into a hard stop.

### Previews

Opt-in per app. `komizo add --preview` installs `komizo-preview` and
`write-preview-stackenv` and grants that app's account the doas rules. The
host needs a `DOMAIN` entry in `/etc/komizo/preview`, optionally per app
(`DOMAIN.<app>`).

A preview stack is `pr-<N>.<domain>` and `pr-<N>-api.<domain>`. Reaping is by
TTL (72h default) and a max-N ceiling, and the reaper touches **only**
previews it holds a state record for — never an app's containers or images.

Products with preview infrastructure provisioned today: cazper, astry, gdam,
termcade.

### Secrets on a host

`KOMIZO_SECRET_<NAME>` environment entries in a CD job are written by the
deploy into `/srv/<app>/secrets.env`, root-owned `0600`, and `env_file`'d into
the containers that need them.

The file is **not** mounted into any container — Docker reads it at create
time — but the values do become container environment, which means they are
visible in `docker inspect` and in `/proc/1/environ`, and are inherited by
child processes. Treat Docker socket access as equivalent to reading every
secret on the box.

`fieldsofrevik` additionally uses a **scoped service-env profile**
(`fields-postgres-v2`): a confined generation directory under
`/srv/fieldsofrevik/secrets/generations/<id>/`, four env files root-owned
`0600`, a `provenance` file root-owned `0400`, `current` as a symlink, and
matching `SCOPED_*` keys in the app record. The CD job must pass
`service-env-profile` and `expected-generation`. It is the only app with this,
and it is the most fragile thing in the fleet: the host, the generation, the
compose file and the action pin all have to agree, and nothing tells you which
one disagrees except the host's own refusal line.

---

## GitHub environments, secrets and variables

### The three levels

| level | reaches | notes |
| --- | --- | --- |
| organization | see the warning below | `visibility: all` or `selected` |
| repository | every job in the repo | |
| environment | only jobs declaring `environment:` | `Production`, `Preview` |

More specific wins: environment over repository over organization.

### ⚠️ Organization secrets do not reach private repositories on the Free plan

`aviorstudio` is on the **Free** plan and all of its repositories are
**private**. Organization secrets and variables are available to private
repositories only on Team and Enterprise plans. So every organization-level
entry in `aviorstudio` is **inert** for every product in it, including the ones
marked `visibility: all`.

The repository-level copies that look like duplicates of them are not
duplicates. They are the only working source. Deleting one breaks the job that
reads it, and the failure is a confusing one — an empty value rather than a
missing-secret error.

This is the single most expensive thing to get wrong here, because the values
are identical, the `selected` list looks right, and nothing in the GitHub UI
says the entry will not apply.

### Secrets are write-only

Nobody can read a secret's value back, including the account that set it —
`gh secret list` returns names only. A deleted secret is gone. **Variables are
readable**, so a variable can always be recorded before it is changed and
restored afterwards; a secret cannot. Treat the two completely differently
when cleaning up.

### Environment names are matched case-insensitively

A job may say `environment: production` and resolve the environment named
`Production`. Both spellings are in use across the fleet and both work. The
API cannot rename an environment, and delete-and-recreate destroys its
secrets, so the mixed casing stays.

### Reusable workflows cannot see environment secrets from the caller

A job that uses `uses: <reusable workflow>` **cannot declare `environment:`**.
Any `${{ secrets.X }}` it passes down is evaluated in the *caller's* context,
which therefore sees only repository and organization level — even when the
called workflow's own job declares an environment.

Consequences, both live today:

- `backup.yml` passes `KOMIZO_DEPLOY_KEY`, `BACKUP_S3_ACCESS_KEY` and
  `BACKUP_S3_SECRET_KEY` from the caller, so those three **must** exist at
  repository level. They cannot be environment-only.
- Variables read *inside* the reusable workflow (`vars.BACKUP_S3_BUCKET`,
  `vars.BACKUP_S3_HOSTNAME`) resolve in the called job, which does declare
  `environment: production`.

So "this name exists at two levels" is not by itself duplication. Check which
consumer reads it and from which context first.

### The convention

Per product:

| name | level | notes |
| --- | --- | --- |
| `CLERK_SECRET_KEY` | Production + Preview | different Clerk instances |
| `CLERK_PUBLISHABLE_KEY` | Production + Preview | `pk_live_` vs `pk_test_` |
| `KOMIZO_DEPLOY_KEY` | Production + Preview, and repo where backup needs it | |
| `KOMIZO_SERVER_URL`, `KOMIZO_KNOWN_HOSTS`, `KOMIZO_APP_NAME` | repository | variables |
| `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY` | repository | caller context |
| `BACKUP_S3_BUCKET`, `BACKUP_S3_HOSTNAME` | repository | variables |
| product-specific | Production | e.g. `TAROT_DRAW_SIGNING_KEY`, `RUNTIME_DATABASE_URL` |

**Production and Preview holding the same name is not duplication** — they
hold different values on purpose. Every product's Preview environment carries
a *development* Clerk instance (`pk_test_`, a `*.clerk.accounts.dev` domain)
while Production carries `pk_live_`.

Because Preview already holds the development instance, a job that declares
`environment: Preview` should read `CLERK_SECRET_KEY` and
`CLERK_PUBLISHABLE_KEY` from it rather than from repository-level
`CLERK_*_DEV`. The `_DEV` pair predates the environments being standardised
and is a weaker-scoped copy of the same credential.

### Auth boundary

`helpers/auth-boundary.mjs` requires that **any job reading a Clerk secret
declares an `environment:`**. The rule used to be about a `_DEV`/`_PROD`
suffix in the name; it is now about the job's scope, which is the thing that
actually protects the value.

---

## Actions and pinning

Three things are pinned, and `helpers/pins.mjs` requires them to agree.

| what | pinned where |
| --- | --- |
| composite actions (`nicodes/komizo-actions/*`) | `uses: ...@<sha>` in the product, recorded in `ACTION-PINS.json` |
| reusable workflows (`nicodes/cicd/.github/workflows/*`) | `uses: ...@<sha>` in the product |
| the helpers those workflows run | `ref:` **inside** the workflow, in this repository |

The third is the one people miss. GitHub resolves the caller's ref for the
workflow file alone; whatever that workflow then checks out comes from the
`ref:` written inside it. Pinning the workflow by SHA pins one file and leaves
the helpers on a different commit. `scripts/release.sh` rewrites those inner
pins mechanically when a release is cut, which is what the old "bump-along"
rule did by hand.

`ACTION-PINS.json` at a product's root records `{tag, sha}` per composite, and
CI refuses a `uses:` that is not recorded, is not a 40-hex commit, or names an
annotated tag object rather than its peeled commit. Adding a new composite to
a workflow means adding its entry here too, or Test fails with
`ACTION-PINS.json has no fleet pin for ...`.

Current composite release: **v0.0.24**
(`bb0afe3d2ef7d6c834da718485256077f53c24b0`).

### Reusable workflows this repository publishes

`backup.yml`, `ci.yml`, `dependabot.yml`, `deployed.yml`,
`tool-maintenance.yml`, `tools.yml`, `vuln.yml` — plus `release.yml`, which is
this repository's own.

### Releases and the fan-out

A release is an immutable `vX.Y.Z` tag on a reviewed `main` commit. Tags never
move. Full procedure in `releases.md`; the shape is:

1. `sh scripts/release.sh prepare vX.Y.Z <main-sha> --push` — **on an
   operator's machine**, never in Actions.
2. Merge the candidate PR unedited.
3. Dispatch the **Release** workflow with the version, the merge SHA and the
   PR number. It verifies CI passed on that exact commit and that the tree
   still matches what `prepare` generated.
4. Bump `snapshot_revision` in `FLEET.json` — `helpers/fleet-baseline.mjs`
   reads it from **main over plain HTTPS**, not from the pinned ref, so the
   baseline moves first and the products follow.
5. `python3 scripts/fanout.py X.Y.Z --push` — again on an operator's machine —
   opens one PR per product moving both pins.

Steps 1 and 5 cannot run in Actions: they edit files under
`.github/workflows`, which `GITHUB_TOKEN` is refused for, and granting
`workflows: write` would mean holding a standing push-to-any-workflow
credential across nine repositories and three owners.

Between step 4 and the last product merging, products still on the previous
revision fail their baseline check. That is what adoption being a sweep means,
and it is loud rather than silent.

**Merge the fan-out PRs in small batches.** Each merge to `main` triggers that
product's CD, which is a real production deploy. Nine at once has previously
produced nine simultaneous deploys and six failures.

---

## mise

Every tool version a product uses is pinned in its `.mise.toml`, and CI
installs them with `jdx/mise-action`. Nothing relies on what happens to be on
a runner.

```toml
[tools]
"http:cicd-engineering" = { version = "0.8.3", url = "...", checksum = "sha256:..." }
bun = "1.4.1"
go = "1.27.1"
node = "24.18.1"
python = "3.13.11"
actionlint = "1.7.12"
shellcheck = "0.11.0"
"go:golang.org/x/vuln/cmd/govulncheck" = "1.7.0"
```

`http:cicd-engineering` is this repository's **engineering snapshot** — the
shared helpers and tests every product runs, published as a release tarball
and *installed*, not vendored. mise verifies the sha256 before extracting, and
`scripts/engineering-env.sh` in each product resolves where it landed and
exports `CICD_ENGINEERING`. Helpers are then invoked as
`python3 "$CICD_ENGINEERING"/helpers/<name>`.

The snapshot version and the workflow pins must name the same cicd commit;
`helpers/pins.mjs` enforces it locally and `helpers/fleet-baseline.mjs`
enforces that every product is on the same one.

> Anything that runs a product's scripts must install mise first. The snapshot
> change on 2026-10-01 added the install step to every product's `cd.yml` but
> not to the shared `backup.yml`, and every nightly backup in the fleet failed
> on `mise: command not found` for three days. Nothing caught it because the
> schedule is that workflow's only caller — no pull request exercises it.

---

## make

The Makefile is the interface. CI runs the same targets a developer runs, and
every target shells through `mise exec --` so the pinned toolchain is used
rather than whatever is installed.

| target | what it does |
| --- | --- |
| `make install` | install dependencies |
| `make dev` | run the stack locally (mprocs panes) |
| `make test` | `scripts/test.sh` — unit tests plus the pin and baseline gates |
| `make build` | `scripts/build.sh` — produces `.artifacts/release/` |
| `make e2e` | `scripts/e2e.sh` — browser tests, depends on `build` |
| `make vuln` | `scripts/vuln.sh` — govulncheck and dependency policy |
| `make check` | `test vuln e2e` — the full gate |
| `make clean` | remove build output |

`make check` is what CI runs and what to run before pushing. Pipe it into
nothing — `make check \| tail` swallows the exit code and a failing check then
looks like a passing one.

`make build` writing `.artifacts/release/` is what makes the deploy path
auditable: `release.py record` registers the archive digest and image IDs,
and `release.py publish` re-validates them before pushing to ghcr. CD and the
PR-preview image push both go through it, so an image that reaches a host was
built by a recorded run.

---

## Backups

`backup.yml` runs nightly per product. It connects to the host, exports a
verified Postgres snapshot, encrypts it, and PUTs it to object storage
(Vultr, bucket `dbbackups`), keeping a copy as a workflow artifact.

All products are Postgres. **PocketBase has been removed everywhere** and is
not coming back; this was a global decision, not a per-app one.

Restore drills and the recovery procedure are in `postgresql-recovery.md`.

---

## Things that have bitten us

- **A green tick is not a working feature.** The backup workflow was green at
  the job level for days while every run failed inside. Check the artifact,
  not the badge.
- **A scheduled-only path has no pull request.** Backups, tool maintenance and
  the vulnerability scan are only exercised by their schedules. They break
  silently and stay broken.
- **The deploy failure message is not the cause.** A scoped deploy reports
  `did not print exactly one deploy: scoped-generation=<32hex> line` for
  several unrelated faults. The host's own `deploy: refusing:` line is the
  real answer, and is now forwarded to CI.
- **Checking out a feature branch and reading it as `main`.** Use a fresh
  clone when auditing what is deployed.
- **Identical values at two levels does not mean one is redundant.** See the
  Free-plan warning and the reusable-workflow caller context above.
