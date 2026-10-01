#!/usr/bin/env bun
/** Check THIS product against the fleet baseline published by nicodes/cicd.

WHY IT RUNS HERE AND NOT THERE. The obvious shape is a job in cicd that reads
all nine products and compares them. It needs a credential: cicd is public and
every product is private, across three organisations, and a workflow's own
GITHUB_TOKEN reaches only its own repository. It also inverts the
relationship -- cicd is a library products call, not a service that reaches
into them.

So the check runs in the product, against a baseline the library publishes.
Agreement with one baseline IS agreement with each other, so no product ever
reads another, nothing needs a secret, and it runs on every pull request
instead of once a week.

The read is an unauthenticated HTTPS GET of a public file. pins.mjs stays
offline and deterministic; this is the online one, like action-pins.mjs.

WHAT IT DELIBERATELY DOES NOT CHECK: base image digests. Dependabot bumps
those constantly and opens the same bump in every product at once, so a table
in the baseline would be wrong between the bump landing in one product and
somebody editing cicd. Digest agreement is helpers/fleet-audit.py, run by an
operator.
*/
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const BASELINE_URL = process.env.FLEET_BASELINE_URL
  ?? 'https://raw.githubusercontent.com/nicodes/cicd/main/FLEET.json';
// A product either vendors the snapshot or installs it; a migrating fleet
// has both kinds at once, so each location is tried in turn. The pin record
// is product-owned and moves to the root when the snapshot stops being a
// directory in the repository.
const SNAPSHOTS = ['scripts/engineering/SOURCE.json'];
const PIN_RECORDS = ['ACTION-PINS.json', 'scripts/engineering/ACTION-PINS.json'];
const PIN_RECORD = PIN_RECORDS[0];

/** Which product is this? CI says so; a checkout has to be asked. */
export function productName(env, remote) {
  if (env.GITHUB_REPOSITORY) return env.GITHUB_REPOSITORY;
  const match = /[:/]([\w.-]+\/[\w.-]+?)(?:\.git)?$/.exec(remote ?? '');
  return match ? match[1] : null;
}

export function readTools(miseToml) {
  const tools = {};
  for (const line of miseToml.split('\n')) {
    const match = /^\s*"?([\w:./-]+)"?\s*=\s*"([^"]+)"/.exec(line);
    if (match) tools[match[1]] = match[2];
  }
  return tools;
}

/** The whole judgement, as a pure function, so the tests need no network. */
export function compare(product, fleet, facts) {
  const problems = [];
  const entry = fleet.products[product];
  if (!entry) {
    // A product nothing declares is a product nothing audits, which is how
    // five snapshot revisions came to be running at once.
    return [`${product} is not listed in nicodes/cicd FLEET.json, so no baseline applies to it. ` +
            `Add it there (with a profile) before relying on this check.`];
  }
  const baseline = fleet.baseline;

  if (facts.snapshotRevision !== baseline.snapshot_revision) {
    problems.push(`vendored cicd revision is ${facts.snapshotRevision ?? '(absent)'}, ` +
      `the fleet is on ${baseline.snapshot_revision}. Re-vendor with ` +
      `helpers/vendor-snapshot.py --revision ${baseline.snapshot_revision}`);
  }

  const expected = { ...baseline.tools.shared, ...(baseline.tools[entry.profile] ?? {}) };
  for (const [tool, want] of Object.entries(expected)) {
    const got = facts.tools[tool];
    if (got !== want) {
      problems.push(`.mise.toml pins ${tool} = ${got ?? '(absent)'}, the ${entry.profile} ` +
        `baseline is ${want}`);
    }
  }

  if (facts.usesKomizoActions) {
    if (!facts.hasPinRecord) {
      problems.push(`this product uses komizo-actions but has no ${PIN_RECORD}. ` +
        `Bootstrap one: bun "$CICD_ENGINEERING"/helpers/action-pins.mjs`);
    }
    for (const [action, tag] of Object.entries(facts.actionPins)) {
      if (tag !== baseline.komizo_actions_tag) {
        problems.push(`komizo-actions/${action} is pinned at ${tag}, the fleet is on ` +
          `${baseline.komizo_actions_tag}`);
      }
    }
  }
  return problems;
}

export function gather(root) {
  const read = (relative) => {
    const full = path.join(root, relative);
    return fs.existsSync(full) ? fs.readFileSync(full, 'utf8') : null;
  };
  const first = (candidates) => {
    for (const candidate of candidates) {
      const found = read(candidate);
      if (found !== null) return found;
    }
    return null;
  };
  // An installed snapshot is named by CICD_ENGINEERING and lives outside the
  // repository, so its SOURCE.json is read from there rather than from root.
  const installed = process.env.CICD_ENGINEERING
    ? path.join(path.resolve(process.env.CICD_ENGINEERING), 'SOURCE.json') : null;
  const source = (installed && fs.existsSync(installed))
    ? fs.readFileSync(installed, 'utf8') : first(SNAPSHOTS);
  const record = first(PIN_RECORDS);
  let actionPins = {};
  if (record) {
    const pins = JSON.parse(record).pins ?? {};
    if (!Array.isArray(pins)) {
      actionPins = Object.fromEntries(Object.entries(pins).map(([k, v]) => [k, v.tag]));
    }
  }
  let uses = false;
  const walk = (dir) => {
    if (!fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (fs.readFileSync(full, 'utf8').includes('komizo-actions/')) uses = true;
    }
  };
  walk(path.join(root, '.github'));
  return {
    snapshotRevision: source ? JSON.parse(source).revision : null,
    tools: readTools(read('.mise.toml') ?? ''),
    hasPinRecord: record !== null,
    actionPins,
    usesKomizoActions: uses,
  };
}

async function fetchBaseline(url) {
  // Three tries. A consistency check that fails open on a flaky network is
  // the decorative gate this whole exercise is about; one that fails closed
  // on a blip is a check people learn to re-run without reading. Retry, then
  // say plainly which of the two things went wrong.
  let last;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const response = await fetch(url);
      if (response.ok) return JSON.parse(await response.text());
      last = new Error(`${url}: HTTP ${response.status}`);
    } catch (error) {
      last = error;
    }
    if (attempt < 3) await new Promise(resolve => setTimeout(resolve, attempt * 1000));
  }
  throw new Error(`could not read the fleet baseline (${last.message}). ` +
    `This is a network failure, NOT a disagreement: nothing about this product was checked.`);
}

if (import.meta.main) {
  const root = process.cwd();
  let remote = '';
  try {
    remote = execFileSync('git', ['remote', 'get-url', 'origin'], { encoding: 'utf8' }).trim();
  } catch { /* not a checkout with a remote; CI provides the name */ }
  const product = productName(process.env, remote);
  assert.ok(product, 'cannot tell which product this is: no GITHUB_REPOSITORY and no git remote');

  const fleet = await fetchBaseline(BASELINE_URL);
  const problems = compare(product, fleet, gather(root));
  if (problems.length === 0) {
    console.log(`${product} agrees with the fleet baseline (cicd ${fleet.baseline.snapshot_revision.slice(0, 10)}).`);
    process.exit(0);
  }
  console.error(`${product} disagrees with the fleet baseline:`);
  for (const problem of problems) console.error(`  - ${problem}`);
  console.error(`\nThe baseline is FLEET.json in nicodes/cicd. If this product is meant to ` +
    `differ, change it there rather than here -- a local exception nothing records is how ` +
    `the fleet came to have five snapshot revisions at once.`);
  process.exit(1);
}
