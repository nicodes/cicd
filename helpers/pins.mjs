import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { verifyDependencyCoverage } from './dependency-coverage.mjs';

const root = process.cwd();
const snapshotRoot = path.join(root, 'scripts/engineering');
const snapshot = JSON.parse(fs.readFileSync(path.join(snapshotRoot, 'SOURCE.json'), 'utf8'));
assert.equal(snapshot.repository, 'https://github.com/nicodes/cicd');
assert.match(snapshot.revision, /^[a-f0-9]{40}$/);
for (const [relative, expected] of Object.entries(snapshot.files)) {
  assert.match(relative, /^(helpers|tests)\/[\w.-]+$/);
  const bytes = fs.readFileSync(path.join(snapshotRoot, relative));
  assert.equal(createHash('sha256').update(bytes).digest('hex'), expected, `shared helper differs from the reviewed snapshot: ${relative}`);
}
const config = Bun.TOML.parse(fs.readFileSync('.mise.toml', 'utf8'));
for (const [name, version] of Object.entries(config.tools)) {
  assert.equal(typeof version, 'string', `${name}: use one exact tool version`);
  assert.match(version, /^\d+\.\d+\.\d+$/, `${name}: ${version} is not an exact version`);
}
const tracked = execFileSync('git', ['ls-files', '-z'], { encoding: 'utf8' }).split('\0').filter(Boolean);
const applications = tracked.filter(file => /^[^/]+\/package\.json$/.test(file)).map(file => path.dirname(file));
assert.ok(applications.includes('app'), 'the product app must be tracked');
for (const directory of applications) {
  const app = JSON.parse(fs.readFileSync(path.join(directory, 'package.json'), 'utf8'));
  assert.equal(app.packageManager, `bun@${config.tools.bun}`, `${directory}: Bun pin differs`);
  assert.ok(fs.existsSync(path.join(directory, 'bun.lock')), `${directory}: commit bun.lock`);
  for (const name of ['package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'bun.lockb']) {
    assert.ok(!fs.existsSync(path.join(directory, name)), `${directory}: remove the competing ${name}`);
  }
}

function walk(directory) {
  if (!fs.existsSync(directory)) return [];
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    if (entry.name === 'node_modules' || entry.name === '.git' || entry.name === '.local') return [];
    const file = path.join(directory, entry.name);
    return entry.isDirectory() ? walk(file) : [file];
  });
}
const workflows = walk('.github').filter(file => /\.ya?ml$/.test(file));
assert.ok(workflows.some(file => file.includes('/workflows/')), 'no workflows to check');
const komizoActions = 'nicodes/komizo-actions';
const komizoUses = [];

// Misspelling guard, additive and network-free. A one-byte typo in a pinned
// reference still satisfies the full-SHA rule but resolves to no repository,
// surfacing only as a startup_failure at dispatch time that CI never sees —
// komizo-be fd23b9c9 carried `nicodes/komozo-actions/publish@<sha>` (0x6f for
// 0x69), invisible until every CD run failed at action resolution. Any org
// within two edits of a portfolio org (docs/ACTIVE-PROJECTS.md) but not equal
// to one, and any reference under a portfolio org within two edits of a known
// fleet uses-target but not equal to it, is a hard error naming the correction.
const portfolioOrgs = ['nicodes', 'aviorstudio', 'astrylogical'];
const fleetUsesRepos = ['nicodes/komizo-actions', 'nicodes/cicd', 'aviorstudio/gdam-actions'];

function editDistance(a, b) {
  const row = Array.from({ length: b.length + 1 }, (_, index) => index);
  for (let i = 1; i <= a.length; i++) {
    let diagonal = row[0];
    row[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const above = row[j];
      row[j] = Math.min(row[j] + 1, row[j - 1] + 1, diagonal + (a[i - 1] === b[j - 1] ? 0 : 1));
      diagonal = above;
    }
  }
  return row[b.length];
}

function closestOf(value, candidates) {
  return candidates.reduce((best, candidate) =>
    editDistance(value, candidate) < editDistance(value, best) ? candidate : best);
}

for (const file of workflows) {
  const document = Bun.YAML.parse(fs.readFileSync(file, 'utf8'));
  function inspect(value) {
    if (!value || typeof value !== 'object') return;
    if (value.concurrency?.queue !== undefined) {
      // GitHub supports queue:max; pinned actionlint does not yet parse it.
      // Product configs suppress only that exact schema diagnostic.
      assert.equal(value.concurrency.queue, 'max', `${file}: unknown concurrency queue`);
      assert.equal(value.concurrency['cancel-in-progress'], false, `${file}: queued production work must not be canceled`);
    }
    if (typeof value.uses === 'string' && !value.uses.startsWith('./')) {
      assert.match(value.uses, /^[\w.-]+\/[\w./-]+@[a-f0-9]{40}$/, `${file}: action must use a full commit SHA: ${value.uses}`);
      const [owner, repository] = value.uses.split('@')[0].split('/');
      if (portfolioOrgs.includes(owner)) {
        const reference = `${owner}/${repository}`;
        const candidates = fleetUsesRepos.filter(repo => repo.startsWith(`${owner}/`));
        if (!candidates.includes(reference) && candidates.length > 0) {
          const closest = closestOf(reference, candidates);
          assert.ok(editDistance(reference, closest) > 2,
            `${file}: '${reference}' is within a two-byte edit of fleet repository '${closest}' — likely a misspelling: ${value.uses}`);
        }
      } else {
        const closest = closestOf(owner, portfolioOrgs);
        assert.ok(editDistance(owner, closest) > 2,
          `${file}: org '${owner}' is within a two-byte edit of portfolio org '${closest}' — likely a misspelling: ${value.uses}`);
      }
      if (value.uses.startsWith(`${komizoActions}/`)) komizoUses.push({ file, uses: value.uses });
    }
    Object.values(value).forEach(inspect);
  }
  inspect(document);
}
// Shared komizo-actions references follow one fleet-wide pin record so two
// products can never run different revisions of the same shared action. The
// record is copied verbatim across products; only peeled commit SHAs are
// recorded (never annotated tag-object SHAs, which also match the 40-hex rule).
const pinRecord = 'scripts/engineering/ACTION-PINS.json';
const pinRecordPath = path.join(snapshotRoot, 'ACTION-PINS.json');
if (komizoUses.length > 0) {
  assert.ok(fs.existsSync(pinRecordPath), `${pinRecord} is missing: this product uses ${komizoActions} actions, so it must carry the fleet pin record (bootstrap one with: bun scripts/engineering/helpers/action-pins.mjs)`);
  const record = JSON.parse(fs.readFileSync(pinRecordPath, 'utf8'));
  assert.equal(record.repository, `https://github.com/${komizoActions}`, `${pinRecord}: repository must be https://github.com/${komizoActions}`);
  assert.ok(record.pins && typeof record.pins === 'object' && !Array.isArray(record.pins), `${pinRecord}: a pins map of action -> {tag, sha} is required`);
  for (const [action, pin] of Object.entries(record.pins)) {
    assert.match(action, /^[\w.-]+$/, `${pinRecord}: invalid action name '${action}'`);
    assert.match(typeof pin?.tag === 'string' ? pin.tag : '', /^v\d+\.\d+\.\d+$/, `${pinRecord}: ${action}: tag must be an exact vX.Y.Z release tag`);
    assert.match(typeof pin?.sha === 'string' ? pin.sha : '', /^[a-f0-9]{40}$/, `${pinRecord}: ${action}: sha must be the full peeled commit SHA of the recorded tag`);
  }
  for (const { file, uses } of komizoUses) {
    const [, action, sha] = /^nicodes\/komizo-actions\/([\w.-]+)@([a-f0-9]{40})$/.exec(uses) ?? [];
    const pin = record.pins[action];
    assert.ok(pin, `${file}: ${pinRecord} has no fleet pin for ${komizoActions}/${action}; record the agreed {tag, sha}`);
    assert.equal(sha, pin.sha, `${file}: ${komizoActions}/${action} is pinned at ${sha} but the fleet record pins ${pin.tag} (${pin.sha}); align the workflow with ${pinRecord} across all products`);
  }
}
for (const file of tracked.filter(file => /(^|[/.])Dockerfile$/.test(file))) {
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    const from = /^FROM\s+(\S+)/i.exec(line)?.[1];
    if (!from || from === 'scratch') continue;
    assert.match(from, /@sha256:[a-f0-9]{64}$/, `${file}: pin the base image digest: ${from}`);
    const go = /^golang:([\d.]+)/.exec(from)?.[1];
    if (go) assert.equal(go, config.tools.go, `${file}: Go builder differs from mise`);
  }
}
for (const file of tracked.filter(file => /(^|\/)go\.mod$/.test(file))) {
  if (!fs.existsSync(file)) continue;
  const module = fs.readFileSync(file, 'utf8');
  assert.equal(/^go ([\d.]+)$/m.exec(module)?.[1], config.tools.go, `${file}: Go version differs from mise`);
  const toolchain = /^toolchain go([\d.]+)$/m.exec(module)?.[1];
  if (toolchain) assert.equal(toolchain, config.tools.go, `${file}: hidden toolchain drift`);
}
console.log(`Exact tool, lockfile, Go, image, and action pins verified in ${root}`);

verifyDependencyCoverage(Bun.YAML.parse(fs.readFileSync('.github/dependabot.yml', 'utf8')),
  execFileSync('git', ['ls-files', '-z'], { encoding: 'utf8' }).split('\0').filter(Boolean),
  Bun.YAML.parse(fs.readFileSync('.github/workflows/bun-updates.yml', 'utf8')));
