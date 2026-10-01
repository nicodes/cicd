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
  const go = sourceFiles(root, { extensions: ['.go'] })
    .filter(file => !file.endsWith('_test.go'));
  // A file is the local path if it is BUILD-TAGGED for it. Matching on the
  // identity string instead was wrong twice over: it depended on a product
  // spelling a constant a particular way -- gdam writes
  // "sk_test_local_development_only" with underscores where termcade uses a
  // hyphen -- and it is the tag, not the string, that the rule is actually
  // about.
  const tagged = go.filter(file => /^\/\/go:build [^\n]*\blocaldev\b/m.test(read(root, file)) &&
                                   !/^\/\/go:build [^\n]*!localdev/m.test(read(root, file)));
  // An untagged file that hands out a development identity is the failure
  // this rule exists for, whatever else the product does.
  const grants = go.filter(file => {
    if (tagged.includes(file)) return false;
    const body = read(root, file);
    return /(LOCAL_AUTH|_DEV\b)/.test(body) &&
           /(dev[A-Za-z]*(ClerkID|UserID|Identity)|local[-_]development|LOCAL_USER_ID)/.test(body);
  });
  if (!tagged.length && !grants.length) {
    // Passing here would be backwards. The contract says local development
    // uses no Clerk tenant; a product with no bypass at all meets that by
    // having no local path, which in practice means developers point a
    // laptop at a real Clerk instance -- the thing the contract forbids.
    return [{
      level: 'fail',
      text: 'no local development identity exists, so working on this product locally means ' +
            'pointing at a real Clerk instance. The contract is that local uses no Clerk tenant: ' +
            "add a bypass behind `//go:build localdev`, as termcade's clerkauth/local_auth.go does.",
    }];
  }
  const problems = [];
  for (const file of grants) {
    problems.push({
      level: 'fail',
      text: `${file} grants a development identity but carries no \`//go:build localdev\` tag, ` +
            `so it is compiled into the production binary and is one environment variable from ` +
            `being live. Move it behind the tag, as termcade's clerkauth/local_auth.go does.`,
    });
  }
  const refusal = go.some(file => {
    const body = read(root, file);
    return /^\/\/go:build !localdev/m.test(body) && /LOCAL_AUTH|_DEV\b/.test(body) &&
           /(return .*(fmt\.Errorf|errors\.New)|panic\()/.test(body);
  });
  if (!refusal) {
    problems.push({
      level: 'fail',
      text: 'no `//go:build !localdev` counterpart refuses to start when the local switch is set. ' +
            'Without it a production binary handed the variable ignores it silently, and nothing ' +
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
  const everything = workflows.map(f => read(root, f)).join('\n');
  if (/CLERK_SECRET_KEY\s*:/.test(everything) && !/CLERK_SECRET_KEY_(DEV|PROD)/.test(everything)) {
    problems.push('a Clerk secret is wired with no _DEV or _PROD suffix, so nothing in the ' +
                  'workflow says which instance it belongs to.');
  }
  return problems;
}

/** Preview must name its own origin, not inherit production's. */
function previewNamesItsOwnOrigin(root) {
  const workflows = sourceFiles(root, { within: ['.github'], extensions: ['.yml', '.yaml'] })
    .filter(file => /preview/i.test(file));
  if (!workflows.length) {
    return [{ level: 'info', text: 'no preview workflow; the preview half of the contract does not apply yet' }];
  }
  const problems = [];
  for (const file of workflows) {
    const body = read(root, file);
    if (!/CLERK_SECRET_KEY_DEV/.test(body)) {
      problems.push({ level: 'fail', text: `${file} deploys a preview without CLERK_SECRET_KEY_DEV; a preview must use the development instance.` });
    }
    if (!/CLERK_AUTHORIZED_PARTIES/.test(body)) {
      problems.push({ level: 'fail', text: `${file} sets no CLERK_AUTHORIZED_PARTIES, so the preview origin is not the one Clerk accepts tokens for.` });
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
  findings.push(...previewNamesItsOwnOrigin(root));
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
