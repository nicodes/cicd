import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { createRequire } from 'node:module';
import { join } from 'node:path';

export function verifySyntheticUser(user, project) {
  assert.match(user.id, /^user_[A-Za-z0-9]+$/);
  assert.equal(user.object, 'user');
  assert.equal(user.external_id, `${project}-engineering-verifier`);
  assert.deepEqual(user.private_metadata, { purpose: 'engineering_verification', project });
  for (const field of ['public_metadata', 'unsafe_metadata']) assert.deepEqual(user[field] || {}, {});
  for (const field of ['password_enabled', 'two_factor_enabled', 'totp_enabled', 'backup_code_enabled', 'banned', 'locked']) {
    assert.equal(user[field], false, `synthetic identity invariant: ${field}`);
  }
  assert(!user.bypass_client_trust && !user.deprovisioned && !user.username);
  assert(!user.primary_phone_number_id && !user.primary_web3_wallet_id);
  for (const field of ['phone_numbers', 'web3_wallets', 'passkeys', 'external_accounts', 'saml_accounts', 'enterprise_accounts']) {
    assert.deepEqual(user[field] || [], [], `synthetic identity invariant: ${field}`);
  }
  assert.equal(user.email_addresses.length, 1);
  const email = user.email_addresses[0];
  assert.match(email.email_address, new RegExp(`^${project}-engineering-[a-f0-9]{24}@example\\.com$`));
  assert.equal(email.reserved, true);
  assert(!email.verification || email.verification.status === 'unverified');
  assert.deepEqual(email.linked_to || [], []);
  assert(!email.matches_sso_connection);
  assert.equal(user.primary_email_address_id, email.id);
}

export async function productionJourney(project, origin, journey) {
  assert(['cazper', 'komizo'].includes(project));
  assert.equal(origin, project === 'cazper' ? 'https://app.cazper.ai' : 'https://app.komizo.dev');
  assert(process.env.CLERK_SECRET_KEY?.startsWith('sk_live_'), 'production Clerk credential is required');
  const api = project === 'cazper' ? 'https://api.cazper.ai' : 'https://api.komizo.dev';
  async function clerk(path, method = 'GET', body, terminal = []) {
    const response = await fetch('https://api.clerk.com/v1' + path, { method,
      headers: { Authorization: `Bearer ${process.env.CLERK_SECRET_KEY}`, 'Clerk-API-Version': '2026-05-12', 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(30000) });
    if (terminal.includes(response.status)) return null;
    if (!response.ok) throw new Error(`Clerk ${method} request failed (HTTP ${response.status})`);
    return response.status === 204 ? null : response.json();
  }
  const external = `${project}-engineering-verifier`;
  const users = await clerk('/users?external_id=' + encodeURIComponent(external) + '&limit=2');
  assert(Array.isArray(users) && users.length <= 1, 'synthetic identity lookup must be unambiguous');
  const user = users[0] || await clerk('/users', 'POST', {
    external_id: external, email_address: [`${project}-engineering-${randomBytes(12).toString('hex')}@example.com`],
    email_address_identification_status: ['reserved'], private_metadata: { purpose: 'engineering_verification', project } });
  // Report invariant names only; assertion values could expose provider data.
  try { verifySyntheticUser(user, project); } catch { throw new Error('The dedicated synthetic identity has unexpected credentials or metadata'); }
  const memberships = await clerk(`/users/${user.id}/organization_memberships?limit=1`);
  assert.equal(memberships.total_count, 0, 'the synthetic identity must not belong to any organization');
  async function retireSessions() {
    const query = `/sessions?user_id=${user.id}&status=active&paginated=true&limit=100`;
    const sessions = await clerk(query);
    assert(Array.isArray(sessions.data) && sessions.total_count === sessions.data.length && sessions.data.length < 100,
      'synthetic session inventory is incomplete');
    for (const session of sessions.data) {
      assert.equal(session.user_id, user.id);
      assert.match(session.id, /^sess_[A-Za-z0-9]+$/);
      await clerk(`/sessions/${session.id}/revoke`, 'POST');
    }
    assert.equal((await clerk(query)).total_count, 0, 'synthetic sessions were not fully retired');
  }
  await retireSessions();
  const require = createRequire(join(process.cwd(), 'app/package.json'));
  const { chromium } = require('playwright');
  const cleanup = [];
  let browser, task, failure;
  try {
    task = await clerk('/agents/tasks', 'POST', { on_behalf_of: { user_id: user.id }, permissions: '*',
      agent_name: external, task_description: 'Automated owned-record production verification',
      redirect_url: origin + '/', session_max_duration_in_seconds: 300 });
    assert.match(task.agent_task_id, /^[A-Za-z0-9_-]{1,100}$/);
    assert.equal(new URL(task.url).protocol, 'https:');
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    // No tracing: authenticated production traces would contain session headers.
    const page = await context.newPage();
    page.setDefaultTimeout(30000);
    page.setDefaultNavigationTimeout(60000);
    let bearer = '', session;
    page.on('request', request => {
      const url = new URL(request.url());
      if (url.origin === api && url.pathname === '/v1/me') bearer = request.headers().authorization || bearer;
    });
    page.on('response', async response => {
      const url = new URL(response.url());
      if (url.origin === api && url.pathname === '/v1/session' && response.ok()) {
        session = await response.json().catch(() => undefined);
      }
    });
    try {
      await page.goto(task.url);
      await page.waitForURL(url => url.origin === origin, { timeout: 60000 });
    } catch { throw new Error('Clerk delegated browser sign-in did not complete'); }
    async function json(path, method = 'GET', body) {
      if (project === 'komizo') assert.equal(session?.record?.clerk_id, user.id, 'application session must belong to the dedicated verifier');
      const credential = project === 'komizo' ? (session?.token ? `Bearer ${session.token}` : '') : bearer;
      assert(credential, 'the real browser must establish its application session');
      const response = await fetch(api + path, { method, headers: { Authorization: credential, 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(30000) });
      assert(response.ok, `owned fixture request failed (HTTP ${response.status})`);
      return response.status === 204 ? null : response.json();
    }
    await journey({ page, origin, json, user, cleanup });
  } catch (error) {
    failure = error;
  } finally {
    const errors = [];
    for (const operation of cleanup.reverse()) {
      try { await operation(); } catch { errors.push('owned fixture cleanup failed'); }
    }
    if (browser) await browser.close().catch(() => errors.push('browser cleanup failed'));
    if (task?.agent_task_id && /^[A-Za-z0-9_-]{1,100}$/.test(task.agent_task_id)) {
      // The pinned API reports 400 for an accepted task and 404 for an absent
      // one. Both end redemption; its resulting sessions are retired below.
      try { await clerk(`/agents/tasks/${task.agent_task_id}/revoke`, 'POST', undefined, [400, 404]); }
      catch { errors.push('delegated task revocation failed'); }
    }
    try { await retireSessions(); } catch { errors.push('synthetic session revocation failed'); }
    if (errors.length) throw new Error(errors.join('; '));
  }
  // Avoid printing raw Playwright navigation errors with delegated URL tokens.
  if (failure) throw new Error('Production browser journey failed; all available cleanup was attempted');
  console.log(`${project}: real Clerk sign-in, owned-record journey, cleanup and task revocation passed.`);
}
