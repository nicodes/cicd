import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

export function verifyDependencyCoverage(config, files, bunWorkflow) {
  assert.equal(config.version, 2, 'Dependabot configuration must use version 2');
  assert.ok(Array.isArray(config.updates), 'Dependabot update coverage is required');
  const required = new Map([
    ['github-actions', new Set(['/'])],
    ['docker', new Set(['/deploy/images'])],
    ['gomod', new Set(files.filter(file => /(^|\/)go\.mod$/.test(file))
      .map(file => path.posix.dirname(file) === '.' ? '/' : '/' + path.posix.dirname(file)))],
  ]);
  assert.ok(bunWorkflow?.on?.schedule?.length, 'Bun native update schedule is required');
  assert.ok(bunWorkflow.on.schedule.every(item => /^\S+(?: \S+){4}$/.test(item.cron)), 'Bun schedule must be explicit');
  const job = bunWorkflow.jobs?.update;
  assert.equal(job?.if, "github.ref == 'refs/heads/main'", 'Bun writes are restricted to main');
  assert.equal(job?.environment, undefined, 'Bun updater must not receive production secrets');
  assert.deepEqual(job?.permissions, { contents: 'read', issues: 'write' }, 'Bun issue reporting must not grant code, PR or workflow writes');
  assert.ok(job?.steps?.some(step => step.run === 'python3 scripts/engineering/helpers/update-bun.py'), 'Pinned Bun updater must actually run');
  assert.ok(job?.steps?.some(step => step.uses?.startsWith('jdx/mise-action@')), 'Bun updater must install repository pins');
  for (const [ecosystem, directories] of required) {
    for (const directory of directories) {
      assert.ok(config.updates.some(update => update['package-ecosystem'] === ecosystem
        && [update.directory, ...(update.directories ?? [])].includes(directory)
        && ['daily', 'weekly', 'monthly'].includes(update.schedule?.interval)),
      `Dependabot must explicitly cover ${ecosystem} ${directory} with a supported schedule`);
    }
  }
}

if (import.meta.main) {
  const config = Bun.YAML.parse(fs.readFileSync('.github/dependabot.yml', 'utf8'));
  const files = execFileSync('git', ['ls-files', '-z'], { encoding: 'utf8' }).split('\0').filter(Boolean);
  const bunWorkflow = Bun.YAML.parse(fs.readFileSync('.github/workflows/bun-updates.yml', 'utf8'));
  verifyDependencyCoverage(config, files, bunWorkflow);
  console.log('Dependency update coverage includes the app, actions, images and every tracked Go module');
}
