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
const app = JSON.parse(fs.readFileSync('app/package.json', 'utf8'));
assert.equal(app.packageManager, `bun@${config.tools.bun}`);
assert.ok(fs.existsSync('app/bun.lock'), 'the app must commit bun.lock');
for (const name of ['package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'bun.lockb']) {
  assert.ok(!fs.existsSync(path.join('app', name)), `remove the competing ${name}`);
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
    }
    Object.values(value).forEach(inspect);
  }
  inspect(document);
}
for (const file of walk('deploy').filter(file => /Dockerfile$/.test(file))) {
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    const from = /^FROM\s+(\S+)/i.exec(line)?.[1];
    if (!from || from === 'scratch') continue;
    assert.match(from, /@sha256:[a-f0-9]{64}$/, `${file}: pin the base image digest: ${from}`);
    const go = /^golang:([\d.]+)/.exec(from)?.[1];
    if (go) assert.equal(go, config.tools.go, `${file}: Go builder differs from mise`);
  }
}
for (const directory of ['api', 'pb', 'cli', 'poc']) {
  const file = path.join(directory, 'go.mod');
  if (!fs.existsSync(file)) continue;
  const module = fs.readFileSync(file, 'utf8');
  assert.equal(/^go ([\d.]+)$/m.exec(module)?.[1], config.tools.go, `${file}: Go version differs from mise`);
  const toolchain = /^toolchain go([\d.]+)$/m.exec(module)?.[1];
  if (toolchain) assert.equal(toolchain, config.tools.go, `${file}: hidden toolchain drift`);
}
console.log(`Exact tool, lockfile, Go, image, and action pins verified in ${root}`);

verifyDependencyCoverage(Bun.YAML.parse(fs.readFileSync('.github/dependabot.yml', 'utf8')),
  execFileSync('git', ['ls-files', '-z'], { encoding: 'utf8' }).split('\0').filter(Boolean));
