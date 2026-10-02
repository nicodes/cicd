# Secrets, variables and environments

An audit of all 100 repositories across the three owners found 24 sensitive
values sitting at repository level, where every workflow run can read them —
including runs of pull requests. Among them were two products' production
Clerk secrets and one product's live production database URL.

None of that was carelessness. It is what happens when the only thing saying
which tenant a secret belongs to is a suffix in its name.

## The rule

**The environment is the boundary. A name is not.**

`CLERK_SECRET_KEY_PROD` versus `_DEV` is a convention, and a convention is a
promise that nothing checks. An environment with a branch policy is enforced
by GitHub: a job running on a pull-request branch that asks for `Production`
is refused before it starts. Once the environment carries the meaning, the
suffix is redundant, so it goes.

Everything else here follows from that one sentence.

## Environments

Exactly two, spelled this way:

| name | allowed branches | reachable by |
| --- | --- | --- |
| `Production` | `main` only | pushes to `main`, scheduled runs |
| `Preview` | all | any same-repository pull request |

`Preview` allows all branches deliberately — a preview deploys a pull
request, so restricting it to `main` would defeat it. That has a
consequence worth stating plainly: **anything in `Preview` is reachable by
anyone who can open a pull request on the repository.** For a same-repository
pull request the author controls the workflow file, so they can read any
secret that environment holds. `Preview` therefore holds preview-grade
credentials only — a development Clerk tenant, a deploy key whose authority
on the host is limited to previews. It never holds a production value.

Environment names are matched case-insensitively, so renaming `production`
to `Production` does not change any `environment:` line in any workflow.

Anything else — `Production_new`, a `copilot` environment nobody uses, an
empty `Preview` on a repository with no previews — is deleted. An empty
environment is worse than no environment: it looks like a boundary in the
settings UI and enforces nothing.

## Where a value lives

| scope | holds | the test |
| --- | --- | --- |
| organisation | nothing sensitive, ever | would a brand-new repository in this org want it on day one? |
| repository | non-sensitive and environment-independent | could this sit in a public README? |
| environment | **everything sensitive, with no exceptions** | — |

An organisation secret is readable by every repository in the organisation,
which for `aviorstudio` is 48 of them. One product's key does not belong
there.

There is one recorded exception. `GDAM_SECRET_KEY` sits at `aviorstudio`
organisation level and stays there: it is not gdam-be's, despite the name.
Fourteen `gd-*` library repositories read it in their `release.yml`, so it
is a genuinely shared publishing credential, and the organisation is where
a shared credential belongs -- moving it would mean fourteen copies to
rotate in step, which is worse. It was nearly deleted during this migration
on the assumption that the name told the truth about the owner. It does
not, and an exception nobody records is an exception somebody else
removes.

A repository secret is readable by every workflow run in that repository,
including pull requests. Declaring `environment:` on the deploying job does
**not** fence a repository-level secret — environment scoping only protects
values stored in the environment. A repository-level copy of a secret that
also exists in an environment silently defeats the environment copy, which
is the state three products were in.

So: if leaking it would matter, it is in an environment. There is no
convenience exception, including for a value two environments share — it is
stored twice.

## Naming

No `_PROD` or `_DEV` suffix. The environment says which.

| was | is |
| --- | --- |
| `CLERK_SECRET_KEY_PROD`, `CLERK_SECRET_KEY_DEV` | `CLERK_SECRET_KEY` |
| `CLERK_PUBLISHABLE_KEY_PROD`, `..._DEV` | `CLERK_PUBLISHABLE_KEY` (a variable) |
| `CAZPER_DATABASE_URL` | `RUNTIME_DATABASE_URL` |
| `OPENAI_SECRET_KEY` | `OPENAI_API_KEY` |

`RUNTIME_DATABASE_URL` is the fleet's name because the preview host injects
it under that name and cannot know each product's own. Three of the four
products with a database already used it.

Secret or variable: **if it is safe in a browser bundle, it is a variable.**
Clerk publishable keys, domains, bucket names, hostnames. A publishable key
still differs between tenants, so it is an environment variable rather than
a repository one.

Product-specific names stay as they are — `TAROT_DRAW_SIGNING_KEY`,
`TERMCADE_OUTER_PROXY_CIDR`, `PB_DATA_VOLUME`. The convention is about where
a value lives and what distinguishes two copies of the same thing, not about
renaming what is already unambiguous.

## What the gate checks

`helpers/auth-boundary.mjs` used to require the suffix:

> a Clerk secret is wired with no `_DEV` or `_PROD` suffix, so nothing in the
> workflow says which instance it belongs to

That rule existed because nothing else said which tenant. Under this
convention it is replaced by the stronger form:

> any job that reads a Clerk secret declares an `environment:`

A name is a promise; an environment is a mechanism. The companion rule — that
one workflow must not be able to reach both tenants — gets simpler at the
same time, because with the suffixes gone there are no two names to collide.

## The target state

Repository variables are the same three everywhere unless the product needs
more: `KOMIZO_APP_NAME`, `KOMIZO_KNOWN_HOSTS`, `KOMIZO_SERVER_URL`. No
repository holds a secret.

| product | `Production` secrets | `Preview` secrets | extra repo variables |
| --- | --- | --- | --- |
| astry-be | `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY`, `CLERK_SECRET_KEY`, `CLERK_WEBHOOK_SECRET`, `KOMIZO_DEPLOY_KEY`, `OPENAI_API_KEY`, `RUNTIME_DATABASE_URL`, `TAROT_DRAW_SIGNING_KEY` | `CLERK_SECRET_KEY`, `KOMIZO_DEPLOY_KEY` | `BACKUP_S3_BUCKET`, `BACKUP_S3_HOSTNAME` |
| cazper-be | `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY`, `CLERK_SECRET_KEY`, `KOMIZO_DEPLOY_KEY`, `OPENAI_API_KEY`, `RUNTIME_DATABASE_URL` | `CLERK_SECRET_KEY`, `KOMIZO_DEPLOY_KEY` | `BACKUP_S3_BUCKET`, `BACKUP_S3_HOSTNAME` |
| gdam-be | `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY`, `CLERK_SECRET_KEY`, `KOMIZO_DEPLOY_KEY`, `RUNTIME_DATABASE_URL` | `CLERK_SECRET_KEY`, `KOMIZO_DEPLOY_KEY` | `API_DOMAIN`, `APP_DOMAIN`, `BACKUP_S3_BUCKET`, `BACKUP_S3_HOSTNAME` |
| termcade-be | `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY`, `CLERK_SECRET_KEY`, `KOMIZO_DEPLOY_KEY`, `RUNTIME_DATABASE_URL`, `TERMCADE_BACKUP_AGE_IDENTITY`, `TERMCADE_OUTER_PROXY_CIDR` | `CLERK_SECRET_KEY`, `KOMIZO_DEPLOY_KEY` | `BACKUP_S3_BUCKET`, `BACKUP_S3_HOSTNAME`, `PB_DATA_VOLUME` |
| fieldsofrevik | `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY`, `CLERK_SECRET_KEY`, `DISCORD_WEBHOOK_URL`, `KOMIZO_DEPLOY_KEY` | `CLERK_SECRET_KEY`, `KOMIZO_DEPLOY_KEY` | `BACKUP_S3_BUCKET`, `BACKUP_S3_HOSTNAME` |
| tonesplit-be | `CLERK_SECRET_KEY`, `KOMIZO_DEPLOY_KEY` | `CLERK_SECRET_KEY`, `KOMIZO_DEPLOY_KEY` | — |
| ctcalc-be | `KOMIZO_DEPLOY_KEY` | `KOMIZO_DEPLOY_KEY` | — |
| castledrop | `KOMIZO_DEPLOY_KEY` | `KOMIZO_DEPLOY_KEY` | — |
| prizm | `KOMIZO_DEPLOY_KEY` | `KOMIZO_DEPLOY_KEY` | — |

Environment variables: `CLERK_PUBLISHABLE_KEY` in both environments of every
product that uses Clerk — astry-be, gdam-be, fieldsofrevik, tonesplit-be, and
cazper-be once its bundle reads it from a variable rather than a literal.

Organisation level, all three owners: nothing. `aviorstudio`'s
`BACKUP_S3_ACCESS_KEY` and `BACKUP_S3_SECRET_KEY` move into each product's
`Production`; `GDAM_SECRET_KEY` moves to `gdam-be`'s; the four organisation
variables become repository variables, which is already how `nicodes` and
`astrylogical` work.

Repositories outside the nine keep one `Production` environment holding
whatever they publish with: `TERMCADE_TOKEN` for termcade-games,
`SUPABASE_ACCESS_TOKEN` for sanctumcrusade and stringtheory, `NPM_TOKEN` for
react-heading. The 28 repositories whose environments are entirely empty
lose them.

## Three deliberate omissions

**No `OPENAI_API_KEY` in `Preview`.** Previews get no image generation. A
billable key in an environment any pull request can read is a different
decision from the one this document is making, and it should be made on
purpose — with a spend-capped key if the answer is yes.

**No `RUNTIME_DATABASE_URL` in `Preview`.** The preview host creates a
database per pull request and injects the DSN itself. A DSN stored in GitHub
would point every preview at one shared database, which is the opposite of
what previews are for.

**The backup S3 credentials are stored five times** rather than once at
organisation level. That is the honest cost of the rule. The better answer is
not an exception but a narrower credential: one scoped to the backup bucket,
so where it lives matters less.

## Migrating

A rename preserves an environment's secrets; deleting and recreating does
not, and nobody — including the person who set them — can read a secret value
back out of GitHub to re-enter it. So:

1. Rename `production` to `Production` in the settings UI. Seven of the nine
   are lowercase. There is no API for this, and recreating would destroy
   values that cannot be recovered.
2. Create the missing `Preview` environments and set both branch policies.
3. Move each repository-level secret into the environment that should hold
   it, then delete the repository-level copy — in that order, never the
   reverse.
4. Rename the suffixed entries, update the workflows that read them, and
   switch the gate rule.

Start with `fieldsofrevik`: six repository-level secrets including both Clerk
tenants, and a `production` environment that is empty, unrestricted and
purely decorative. If the pattern survives that one it survives anywhere.
