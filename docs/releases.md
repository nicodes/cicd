# Releases

Until now this repository had no releases. Eight products depended on it and
pinned arbitrary commits off `main`, which meant there was no reviewed unit,
no changelog, and no way to answer "which version is astry on". They ended up
spread across five revisions with nothing reporting it.

A release is an immutable `vX.Y.Z` tag on a reviewed `main` commit. No tag
ever moves.

## The three pins, and which one this fixes

A product consumes this repository three ways at once:

| what | pinned where |
| --- | --- |
| reusable workflows | `uses: nicodes/cicd/.github/workflows/backup.yml@<sha>` in the product |
| vendored helpers | `revision` in the product's `scripts/engineering/SOURCE.json` |
| **the helpers those workflows run** | **`ref:` inside the workflow, in THIS repository** |

The third is the one people miss. GitHub resolves the caller's ref for
`backup.yml` alone; the helpers it then checks out come from whatever `ref:`
is written inside it. A consumer who pinned `backup.yml` by SHA pinned one
file and left the helpers on a different, older commit.

The README's bump-along rule is the manual answer — a PR cannot name its own
merge SHA, so the *next* PR bumps the pins. It works, and it has already
failed once in production: a stale pin ran an older portfolio identity check
and rejected a new adopter at the Report-failure step. `scripts/release.sh`
does that rewrite mechanically instead, as part of cutting a release.

Keeping the first two in agreement is the product's job, checked by
`helpers/pins.mjs`.

## Cutting one

**1. Prepare — on your own machine, not in Actions.** Choose an unused
`vX.Y.Z` and the full current `main` SHA. Both are explicit and must not
change on a retry.

```sh
sh scripts/release.sh prepare v0.1.0 <full-current-main-sha> --push
```

This stage cannot run in Actions. The candidate edits files under
`.github/workflows`, and `GITHUB_TOKEN` is refused when it pushes one:

```
refusing to allow a GitHub App to create or update workflow
`.github/workflows/backup.yml` without `workflows` permission
```

That permission is not grantable to the job token — it needs a PAT or an App
credential, which is a standing push-to-any-workflow secret this repository
does not need to hold. komizo-actions runs its prepare in Actions because
what it rewrites is `*/action.yml`, which is not a workflow file; the same
design does not transfer here.

It creates `release/vX.Y.Z` with one deterministic commit that repins every
`nicodes/cicd` self-checkout to the source commit, and prints the candidate,
source and tree ids. It pushes nothing without `--push`, and it refuses to
run if the version exists, if the source is not the tip of `main`, if it
repinned nothing, or if any self-checkout pin survived.

**2. Open the PR** it printed, with your own identity, and merge it through
the normal protected path. Do not edit the candidate.

**3. Publish.** Wait for CI to pass on the exact merge commit, then dispatch
the **Release** workflow with the same version, that merged SHA, and the PR
number. This half only writes a tag, so the job token is enough. It verifies the PR was merged into this repository, that CI succeeded
on that exact commit, and that the tree still matches what `prepare`
generated — a change introduced during merge fails closed. Then it creates
the annotated tag and the GitHub release.

## Consuming one

`helpers/pins.mjs` enforces the half that belongs to the product: every
`nicodes/cicd/...@<sha>` in its workflows must equal its `SOURCE.json`
revision. Bump both in one pull request.

Pin the **commit**, with the version in a trailing comment:

```yaml
uses: nicodes/cicd/.github/workflows/backup.yml@<40-hex> # v0.1.0
```

A SHA is stronger than a tag — a tag can in principle be deleted and
recreated, a commit cannot — and the comment is what makes the SHA legible in
review. Record the same revision in `SOURCE.json` when you re-vendor;
`pins.mjs` fails when the two disagree, because a product running helper code
from one revision and reusable workflows from another is a product nobody can
describe.

Note for the annotated-tag trap: `git rev-parse v0.1.0` gives the **tag
object**, not the commit. Pin `git rev-parse v0.1.0^{commit}`. Both resolve
to the same tree for `uses:`, but they are different 40-hex strings, and a
fleet pin record comparing strings will read two identical deployments as
disagreeing. komizo-actions already has this split in the wild: prizm and
castledrop pin the tag object for v0.0.1, everyone else pins the commit.
