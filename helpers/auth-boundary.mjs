import fs from 'node:fs';
import path from 'node:path';

/**
 * Three environments, three identity sources, and the boundaries between them.
 *
 *   local       a fixed development user, no Clerk tenant at all
 *   preview     the Clerk DEVELOPMENT instance, per-PR origin
 *   production  the Clerk PRODUCTION instance
 *
 * Every product already intends this. What was missing is anything that
 * notices when one of them stops doing it, which is how the fleet ended up
 * with the local half in one product and the preview half in another.
 *
 * WHAT THIS DOES NOT DO: it does not supply a shared auth implementation.
 * The products verify tokens differently because they are different
 * programs; the thing worth sharing is the contract and the check, not the
 * code. That is also why every rule below reads the product's own tree
 * rather than requiring a particular library.
 *
 * The rules are ordered by what they protect, strongest first.
 */

const CLERK_SECRET = /CLERK_SECRET_KEY(_DEV|_PROD)?\b/g;

/** Files worth reading for a rule, skipping everything generated or vendored. */
export function sourceFiles(root, { within = ['.'], extensions = null } = {}) {
  const found = [];
  const skip = new Set(['node_modules', '.git', 'dist', 'build', '.expo', '__pycache__',
                        '.artifacts', 'vendor', 'scripts/engineering']);
  const walk = (dir, depth) => {
    if (depth > 12) return;
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      const relative = path.relative(root, full);
      if (skip.has(entry.name) || skip.has(relative)) continue;
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) walk(full, depth + 1);
      else if (!extensions || extensions.some(e => entry.name.endsWith(e))) found.push(relative);
    }
  };
  for (const start of within) {
    const base = path.join(root, start);
    if (fs.existsSync(base)) walk(base, 0);
  }
  return found.sort();
}

const read = (root, relative) => {
  try {
    return fs.readFileSync(path.join(root, relative), 'utf8');
  } catch {
    return '';
  }
};

/**
 * A product that says it does not use Clerk must not use Clerk.
 *
 * Declared rather than inferred, so that adopting an identity provider
 * anywhere in the fleet is an edit to FLEET.json and therefore a review,
 * instead of something that appears in a product one afternoon.
 */
function noProvider(root, files) {
  const using = files.filter(file => /\bclerk\b/i.test(read(root, file)));
  if (!using.length) return [];
  return [`declared auth.provider "none" but ${using.length} file(s) reference Clerk, ` +
          `starting with ${using.slice(0, 3).join(', ')}. Either remove them or declare the ` +
          `provider in FLEET.json, which is a reviewed change rather than a silent one.`];
}

/**
 * The local bypass must be compiled out of production, not switched off in it.
 *
 * A runtime flag is one environment variable away from being on in
 * production. A build tag is not there at all. termcade already does this:
 * clerkauth/local_auth.go is `//go:build localdev`, and the `!localdev`
 * counterpart REFUSES TO START if the switch is even present, so a
 * production binary handed the variable stops rather than quietly trusting
 * nobody.
 */
function bypassIsCompiledOut(root) {
  const go = sourceFiles(root, { extensions: ['.go'] }).filter(file => !file.endsWith('_test.go'));

  // STRUCTURE, NOT CONTENT. Earlier versions of this rule tried to find the
  // grant by pattern -- a dev identity constant, a switch name -- and were
  // wrong three times running: they failed termcade over a client-side
  // variable, passed products that had no local path at all, refused gdam
  // because it spells a key with underscores, and finally flagged the call
  // site of a correctly tagged grant because the helper was named
  // devIdentity. Every one of those was a guess about content.
  //
  // What source can actually establish is structure: a local path exists and
  // is behind the tag, and a counterpart refuses the switch when it is not.
  // Whether a shipped binary can produce a development identity is a
  // question about the artifact, and belongs to the image scan, which takes
  // the built thing apart instead of reading about it.
  const tagged = go.filter(file => /^\/\/go:build [^\n]*\blocaldev\b/m.test(read(root, file)) &&
                                   !/^\/\/go:build [^\n]*!localdev/m.test(read(root, file)));
  const refusal = go.some(file => {
    const body = read(root, file);
    return /^\/\/go:build [^\n]*!localdev/m.test(body) &&
           /(LOCAL_AUTH|_DEV\b)/.test(body) &&
           /(return .*(fmt\.Errorf|errors\.New)|panic\()/.test(body);
  });

  const problems = [];
  if (!tagged.length) {
    problems.push({
      level: 'fail',
      text: 'no local development path behind `//go:build localdev`, so working on this ' +
            'product locally means pointing at a real Clerk instance. The contract is that ' +
            "local uses no Clerk tenant: add one, as termcade's clerkauth/local_auth.go does.",
    });
  }
  if (!refusal) {
    problems.push({
      level: 'fail',
      text: 'no `//go:build !localdev` counterpart refuses the local switch. Without it a ' +
            'production binary handed the variable ignores it silently, and nothing ' +
            'distinguishes "the bypass is absent" from "the bypass did not trigger".',
    });
  }
  return problems;
}

/**
 * The SERVER may not take a browser-readable variable as proof of anything.
 *
 * EXPO_PUBLIC_* and NEXT_PUBLIC_* are inlined into JavaScript a visitor can
 * read and set, so a server that trusts one is trusting the client.
 *
 * The first version of this rule flagged the NAME wherever it appeared, and
 * so failed termcade -- the product that implements this best. Its
 * EXPO_PUBLIC_LOCAL_AUTH_URL only tells the app which local issuer to talk
 * to; the Go side reads its own TERMCADE_LOCAL_AUTH_URL, demands a loopback
 * address, and is behind a build tag that is not in the production binary.
 * A client-side variable that no server trusts grants nothing. What matters
 * is whether server code reads it, so that is what this checks.
 */
function noPublicBypassFlag(root, files) {
  const serverFiles = files.filter(file => file.endsWith('.go') && !file.endsWith('_test.go'));
  const offenders = [];
  for (const file of serverFiles) {
    for (const [, name] of read(root, file).matchAll(/\b((?:EXPO|NEXT|VITE)_PUBLIC_[A-Z0-9_]*)\b/g)) {
      offenders.push(`${file}: ${name}`);
    }
  }
  return offenders.length
    ? [`server code reads a browser-readable variable: ${[...new Set(offenders)].slice(0, 4).join(', ')}. ` +
       `Anything with a public prefix is inlined into the bundle, so a visitor can set it; a server ` +
       `must take its own non-public variable instead.`]
    : [];
}

/**
 * Development and production credentials must not meet.
 *
 * The preview path takes _DEV, the production deploy path takes _PROD, and
 * neither names the other. A single workflow that can reach both is one
 * `if:` away from sending production credentials to a pull request.
 */
function keysDoNotCross(root) {
  const workflows = sourceFiles(root, { within: ['.github'], extensions: ['.yml', '.yaml'] });
  const problems = [];
  for (const file of workflows) {
    const body = read(root, file);
    const names = new Set([...body.matchAll(CLERK_SECRET)].map(m => m[0]));
    if (names.has('CLERK_SECRET_KEY_DEV') && names.has('CLERK_SECRET_KEY_PROD')) {
      problems.push(`${file} can reach both CLERK_SECRET_KEY_DEV and CLERK_SECRET_KEY_PROD. ` +
                    `Keep the preview path and the production path in separate workflows, so the ` +
                    `boundary is the file rather than a condition inside it.`);
    }
  }
  // A job that reads a Clerk secret must declare an environment.
  //
  // This replaced "the name carries a _DEV or _PROD suffix". That rule asked
  // the name to say which Clerk instance a value belonged to, and a name
  // promises nothing -- the suffix could be wrong and the check would pass.
  // An environment is a mechanism: GitHub refuses a pull-request branch that
  // asks for an environment whose branch policy is main, so a preview cannot
  // reach the production tenant's secret even by naming it. Once the
  // environment carries the meaning, the suffix is redundant, and the fleet
  // dropped it -- see docs/secrets.md.
  for (const file of workflows) {
    for (const [name, body] of jobsOf(read(root, file))) {
      if (!CLERK_SECRET.test(body)) { CLERK_SECRET.lastIndex = 0; continue; }
      CLERK_SECRET.lastIndex = 0;
      if (!/^\s{4}environment:/m.test(body)) {
        problems.push(`${file} job "${name}" reads a Clerk secret without declaring an ` +
                      `environment:, so nothing decides which Clerk instance it may reach. ` +
                      `Add environment: Production or environment: Preview to the job.`);
      }
    }
  }
  return problems;
}

/**
 * The jobs of a workflow, as [name, body] pairs.
 *
 * Split textually rather than parsed: this file has no YAML dependency and
 * runs against nine products' workflows, so it reads what is written rather
 * than what a parser would normalise. A job key is two spaces deep under
 * `jobs:`; its body runs to the next one.
 */
function jobsOf(text) {
  const afterJobs = text.split(/^jobs:\s*$/m)[1];
  if (!afterJobs) return [];
  const out = [];
  const starts = [...afterJobs.matchAll(/^  ([A-Za-z_][\w-]*):\s*$/gm)];
  for (let i = 0; i < starts.length; i++) {
    const from = starts[i].index + starts[i][0].length;
    const to = i + 1 < starts.length ? starts[i + 1].index : afterJobs.length;
    out.push([starts[i][1], afterJobs.slice(from, to)]);
  }
  return out;
}

/**
 * Preview must name its own origin, not inherit production's.
 *
 * The rule is structural, not a name match. A product may spell the azp
 * allowlist `CLERK_AUTHORIZED_PARTIES`, or it may parse one origin policy
 * and hand the same value to both CORS and Clerk's authorized-party
 * handler -- astry does the latter, and an earlier version of this check
 * failed it for using a different, equally correct name. So: find the
 * variables the preview workflow writes a computed per-PR origin into, and
 * require the server to route one of them into its azp decision.
 */
function previewNamesItsOwnOrigin(root, files) {
  const workflows = sourceFiles(root, { within: ['.github'], extensions: ['.yml', '.yaml'] })
    .filter(file => /preview/i.test(file));
  if (!workflows.length) {
    return [{ level: 'info', text: 'no preview workflow; the preview half of the contract does not apply yet' }];
  }
  const go = files.filter(file => file.endsWith('.go'));
  const azpDecision = go.filter(file => /AuthorizedPartyHandler|authorizedPart|\bazp\b/i.test(read(root, file)));
  const goBodies = go.map(file => read(root, file)).join('\n');

  const problems = [];
  for (const file of workflows) {
    const body = read(root, file);
    // Two ways to name the development instance, and the contract is about
    // WHICH INSTANCE a preview reaches rather than which spelling it uses.
    //
    //   1. secrets.CLERK_SECRET_KEY_DEV, a repository-level entry.
    //   2. secrets.CLERK_SECRET_KEY read by a job that declares
    //      environment: Preview -- that environment holds the development
    //      instance on every product, and scoping it there is strictly
    //      better: the value is behind the environment boundary instead of
    //      readable by every job in the repository.
    //
    // The second is only safe BECAUSE of the environment declaration. The
    // same expression in a job without one resolves at repository level,
    // which is production's key, so that case is a failure and not a pass.
    const usesDevName = /CLERK_SECRET_KEY_DEV/.test(body);
    const previewScoped = [...jobsOf(body)].some(([, job]) =>
      /secrets\.CLERK_SECRET_KEY(?!_)/.test(job) && /^\s{4}environment:\s*["']?[Pp]review\b/m.test(job));
    const unscopedPlainKey = [...jobsOf(body)].some(([, job]) =>
      /secrets\.CLERK_SECRET_KEY(?!_)/.test(job) && !/^\s{4}environment:/m.test(job));
    if (unscopedPlainKey) {
      problems.push({ level: 'fail', text: `${file} reads secrets.CLERK_SECRET_KEY in a job with no environment:, which resolves to the repository-level production key. Declare environment: Preview, or use CLERK_SECRET_KEY_DEV.` });
    } else if (!usesDevName && !previewScoped) {
      problems.push({ level: 'fail', text: `${file} deploys a preview without the development instance; use CLERK_SECRET_KEY_DEV, or read secrets.CLERK_SECRET_KEY from a job declaring environment: Preview.` });
    }
    // Names the workflow assigns a computed origin to, on one line:
    // `printf 'NAME=%s\n' "$app_origin"`, `NAME: ${{ ... }}.preview...`, etc.
    const looksLikeOrigin = line => /https?:\/\//.test(line) || /\$\{?[A-Za-z0-9_]*origin/i.test(line);
    const carriers = new Set();
    for (const line of body.split('\n')) {
      if (!looksLikeOrigin(line)) continue;
      for (const match of line.matchAll(/\b([A-Z][A-Z0-9_]{2,})\s*[=:]/g)) carriers.add(match[1]);
    }
    if (!carriers.size) {
      problems.push({ level: 'fail', text: `${file} never computes a per-PR origin, so the preview cannot be the origin Clerk accepts tokens for.` });
      continue;
    }
    // A preview that names a fixed origin is naming production's. The origin
    // has to be derived from the pull request, or previews share a tenant
    // boundary with prod and with each other.
    const namesThePullRequest = line =>
      /PR_NUMBER|pull_request\.number|github\.event\.number|PREVIEW_ID/i.test(line) ||
      /\bpr-[^.\/\s"']+\./i.test(line);
    const perPullRequest = body.split('\n').some(line => looksLikeOrigin(line) && namesThePullRequest(line));
    if (!perPullRequest) {
      problems.push({ level: 'fail', text: `${file} computes a preview origin that does not vary per pull request, so the preview answers for a shared -- in practice production's -- origin.` });
      continue;
    }
    if (!azpDecision.length) {
      // No server here to check an azp claim; the workflow half is all there is.
      continue;
    }
    const routed = [...carriers].filter(name => goBodies.includes(name));
    if (!routed.length) {
      problems.push({ level: 'fail', text: `${file} computes a preview origin into ${[...carriers].sort().join(', ')}, but no Go source reads any of them, so the origin never reaches Clerk's authorized-party check.` });
    }
  }
  return problems;
}

/**
 * Check one product against the contract.
 *
 * Returns findings, each with a level: `fail` is a boundary broken, `info`
 * is a part of the contract that does not apply here yet. The caller decides
 * whether a failure is fatal -- FLEET.json's `enforced` flag is how the
 * fleet adopts this one product at a time rather than turning nine
 * repositories red at once.
 */
export function checkAuthBoundary(root, declared) {
  if (!declared) {
    return [{ level: 'fail', text: 'this product has no auth declaration in FLEET.json. Add one (provider, api, enforced) so the fleet records what its identity boundary is.' }];
  }
  const files = sourceFiles(root, {
    within: ['api', 'app', 'src', 'scripts', 'golang_api', 'web', 'e2e'],
    extensions: ['.go', '.ts', '.tsx', '.js', '.mjs', '.cjs', '.svelte', '.gd'],
  });

  if (declared.provider === 'none') {
    return noProvider(root, files).map(text => ({ level: 'fail', text }));
  }
  if (declared.provider !== 'clerk') {
    return [{ level: 'fail', text: `unknown auth provider "${declared.provider}"` }];
  }

  const findings = [];
  findings.push(...noPublicBypassFlag(root, files).map(text => ({ level: 'fail', text })));
  findings.push(...previewNamesItsOwnOrigin(root, files));
  if (declared.api) {
    findings.push(...bypassIsCompiledOut(root));
    findings.push(...keysDoNotCross(root).map(text => ({ level: 'fail', text })));
  } else {
    findings.push({ level: 'info', text: 'declared api: false -- no server verifies a token here, so there is no secret to route and no bypass to compile out' });
  }
  return findings;
}

export function report(product, findings, enforced) {
  const failures = findings.filter(f => f.level === 'fail');
  const lines = [];
  for (const finding of findings) {
    lines.push(`  ${finding.level === 'fail' ? '✗' : '·'} ${finding.text}`);
  }
  if (!failures.length) {
    return { text: `${product} holds the auth boundary contract.${lines.length ? '\n' + lines.join('\n') : ''}`, failed: false };
  }
  const head = enforced
    ? `${product} breaks the auth boundary contract:`
    : `${product} does not hold the auth boundary contract yet (not enforced, so this is a report):`;
  return { text: `${head}\n${lines.join('\n')}`, failed: enforced };
}

if (import.meta.main) {
  // The same unauthenticated fetch fleet-baseline.mjs uses: FLEET.json is
  // public, every product is private, and a check that needed a token could
  // not run on every pull request across three owners.
  const { productName, fetchBaseline, BASELINE_URL } = await import('./fleet-baseline.mjs');
  const { execFileSync } = await import('node:child_process');
  let remote = '';
  try {
    remote = execFileSync('git', ['remote', 'get-url', 'origin'], { encoding: 'utf8' }).trim();
  } catch { /* CI supplies the name */ }
  const product = productName(process.env, remote);
  if (!product) {
    console.error('cannot tell which product this is: no GITHUB_REPOSITORY and no git remote');
    process.exit(1);
  }
  const fleet = await fetchBaseline(BASELINE_URL);
  const declared = fleet.products?.[product]?.auth;
  const findings = checkAuthBoundary(process.cwd(), declared);
  const { text, failed } = report(product, findings, declared?.enforced);
  (failed ? console.error : console.log)(text);
  if (failed) {
    console.error('\nThe contract is in FLEET.json under auth_contract: local uses no Clerk ' +
      'tenant, preview uses the development instance, production uses the production one.');
  }
  process.exit(failed ? 1 : 0);
}
