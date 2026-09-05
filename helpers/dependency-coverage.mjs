import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

export function verifyDependencyCoverage(config, files) {
  assert.equal(config.version, 2, 'Dependabot configuration must use version 2');
  assert.ok(Array.isArray(config.updates), 'Dependabot update coverage is required');
  const required = new Map([
    ['github-actions', new Set(['/'])],
    ['bun', new Set(['/app'])],
    ['docker', new Set(['/deploy/images'])],
    ['gomod', new Set(files.filter(file => /(^|\/)go\.mod$/.test(file))
      .map(file => path.posix.dirname(file) === '.' ? '/' : '/' + path.posix.dirname(file)))],
  ]);
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
  verifyDependencyCoverage(config, files);
  console.log('Dependency update coverage includes the app, actions, images and every tracked Go module');
}
