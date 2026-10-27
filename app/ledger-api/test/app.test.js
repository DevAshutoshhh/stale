'use strict';
const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { createApp } = require('../src/app');
const { parseTokens } = require('../src/routes/balance');
const { deriveState, createClaimsStatus, apiParts } = require('../src/claims-status');
const { html } = require('../src/routes/status');

const BALANCES = { alpha: 10000, beta: 25000 };
const queries = [];
async function fakeQuery(text, params) {
  queries.push({ text, params });
  if (text.includes('SUM(amount_cents)')) return { rows: [{ balance: String(BALANCES[params[0]] || 0) }] };
  if (text.includes('schema_migrations')) return { rows: [{ v: '20261020090000' }] };
  if (text.includes('reltuples')) return { rows: [{ n: '850004' }] };
  if (text.includes('ledger_entries_merchant_idx')) return { rows: [{ valid: true }] };
  if (text.includes('indisvalid')) return { rows: [] };
  throw new Error('unexpected query');
}

let server, base;
before(async () => {
  const app = createApp({
    query: fakeQuery, tokens: parseTokens('alpha:tok-alpha,beta:tok-beta'), revision: () => 'rev-test',
    threshold: 500000,
    liveFeeds: async () => ({ error: null,
      releases: [{ version: process.version, security: true }, { version: 'v1.0.0', security: true }],
      roots: { distrusted: [{ spki_sha256: 'f'.repeat(64) }] } }),
    partners: { trusted: 'https://localhost:1/hook', distrusted: 'https://localhost:1/hook' },
    claimsStatus: async () => ({ source: 'test', error: null, project_url: 'https://gitlab.example/p',
      claims: [{ id: 'anchor-trusted', state: 'VALID', minted_at: '2026-10-25T00:00:00Z', revision: 'rev-test',
        fingerprint: 'sha256:abc', open_issue_url: null, issue_url: null, mr_urls: [], pipeline_url: null }],
      sci: { reproofs_skipped: 0, runner_seconds_avoided: 0 } }),
  });
  server = app.listen(0);
  await new Promise(r => server.once('listening', r));
  base = `http://127.0.0.1:${server.address().port}`;
});
after(() => server.close());

test('healthz', async () => {
  const r = await fetch(`${base}/healthz`);
  assert.equal(r.status, 200);
  assert.equal(await r.text(), 'ok');
});

test('balance: each user sees only their own balance, never cached', async () => {
  for (const [user, cents] of Object.entries(BALANCES)) {
    const r = await fetch(`${base}/api/v1/balance`, { headers: { authorization: `Bearer tok-${user}` } });
    assert.equal(r.status, 200);
    assert.deepEqual(await r.json(), { user_id: user, balance_cents: cents, revision: 'rev-test' });
    assert.equal(r.headers.get('cache-control'), 'private, no-store');
    assert.equal(r.headers.get('vary'), 'Authorization');
  }
  assert.deepEqual(queries.filter(q => q.text.includes('SUM')).map(q => q.params), [['alpha'], ['beta']]);
});

test('balance: missing or unknown token is 401 and still no-store', async () => {
  for (const headers of [{}, { authorization: 'Bearer nope' }, { authorization: 'tok-alpha' }]) {
    const r = await fetch(`${base}/api/v1/balance`, { headers });
    assert.equal(r.status, 401);
    assert.equal(r.headers.get('cache-control'), 'private, no-store');
  }
});

test('webhooks: unknown target is 400; unreachable partner is 502 with an error code', async () => {
  let r = await fetch(`${base}/api/v1/webhooks/deliver`, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ target: 'nope' }) });
  assert.equal(r.status, 400);
  r = await fetch(`${base}/api/v1/webhooks/deliver`, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ target: 'trusted' }) });
  assert.equal(r.status, 502);
  const body = await r.json();
  assert.equal(body.delivered, false);
  assert.equal(body.tls_error, 'ECONNREFUSED');
});

test('internal/runtime returns the report shape', async () => {
  const r = await (await fetch(`${base}/internal/runtime`)).json();
  for (const k of ['node_version', 'revision', 'trust_policy', 'ca_source_flags', 'effective_ca_spki',
    'effective_ca_subjects', 'effective_ca_keys']) assert.ok(k in r, k);
  assert.equal(r.node_version, process.version);
});

test('status.json and status html', async () => {
  const j = await (await fetch(`${base}/status.json`)).json();
  assert.equal(j.schema_version, '20261020090000');
  assert.deepEqual(j.invalid_indexes, []);
  assert.equal(j.claims[0].state, 'VALID');
  const ev = j.claims[0].evidence;
  assert.equal(ev[0].label, 'Node release');
  assert.equal(ev[0].ok, true, 'running Node equals the floor in the fake feed');
  assert.equal(ev[1].value, 'none');
  const page = await (await fetch(`${base}/status`)).text();
  assert.match(page, /anchor-trusted/);
  assert.match(page, /How the loop works/);
  assert.match(page, /Live loop activity/);
  assert.match(page, /What changed in the world/);
  assert.match(page, /ledger_entries ≈ 850,004 rows/);
  assert.equal(j.world.length, 3);
  assert.match(page, /http-equiv="refresh" content="30"/);
  assert.doesNotMatch(page, /<script|<link|src="http/);  // no external assets
});

test('deriveState follows the SRS §6.4 order', () => {
  assert.equal(deriveState({ labels: ['stale-state::quarantined', 'stale-state::reproving'] }, true), 'QUARANTINED');
  assert.equal(deriveState({ labels: ['stale-state::retry-pending'] }, true), 'REMEDIATING');
  assert.equal(deriveState({ labels: [] }, true), 'STALE');
  assert.equal(deriveState(null, true), 'VALID');
  assert.equal(deriveState(null, false), 'NOT MINTED');
});

test('claims status reads the lockfile and open issues, caches for 30 s', async () => {
  let t = 0, calls = 0;
  const url = 'https://gl.test/api/v4/projects/42/repository/files/.stale%2Fclaims.lock.json/raw?ref=main';
  const fetchImpl = async u => {
    calls++;
    const body = u.includes('merge_requests') ? [{ iid: 7, state: 'opened', web_url: 'https://gl.test/g/p/-/merge_requests/7', description: 'Fix.\n\nRelated to #4' }]
      : u.includes('source=parent_pipeline') ? [{ id: 11, created_at: '2026-10-26T17:19:00Z', web_url: 'https://gl.test/p/11' }, { id: 12, status: 'running', web_url: 'https://gl.test/p/12' }]
      : u.includes('/pipelines/11/jobs') ? [{ name: 'release-deferred', status: 'success' }]
      : u.includes('/pipelines/12/jobs') ? [{ name: 'deploy-canary', status: 'success', web_url: 'j1' }, { name: 'canary-ring', status: 'running' }, { name: 'rollback', status: 'created' }]
      : u === url ? { claims: { 'anchor-trusted': { minted_at: 'x', issue_iid: 3, mr_iids: [5], pipeline_id: 9 },
                                         'migration-safe': { minted_at: null, mr_iids: [] } }, sci: {} }
      : u.endsWith('/projects/42') ? { web_url: 'https://gl.test/g/p' }
      : u.includes('/pipelines') ? [{ name: 'release (manual)', status: 'success' }, { name: 'control-loop', status: 'success', created_at: '2026-10-26T23:19:00Z', web_url: 'https://gl.test/p/1' }]
      : u.includes('state=all') ? [{ iid: 4, title: '[stale] anchor-trusted: R1', state: 'closed', labels: ['stale-claim::anchor-trusted'], created_at: '2026-10-26T16:15:00Z', closed_at: '2026-10-26T16:37:00Z', web_url: 'https://gl.test/g/p/-/issues/4' },
                                    { iid: 5, title: '[policy-check] x', state: 'closed', labels: ['stale-claim::anchor-trusted'] }]
      : [{ labels: ['stale-claim::migration-safe', 'stale-state::reproving'], web_url: 'https://gl.test/g/p/-/issues/4' }];
    return { ok: true, json: async () => body };
  };
  const cs = createClaimsStatus({ lockfileUrl: url, fetchImpl, now: () => t });
  const s = await cs();
  assert.equal(s.claims[0].state, 'VALID');
  assert.equal(s.claims[0].mr_urls[0], 'https://gl.test/g/p/-/merge_requests/5');
  assert.equal(s.claims[1].state, 'REPROVING');
  assert.equal(s.activity.length, 1, 'policy-check issues are not shown');
  assert.equal(s.activity[0].claim, 'anchor-trusted');
  assert.equal(s.control_loop.status, 'success');
  assert.deepEqual(s.remediation_mrs, [{ iid: 7, state: 'opened', web_url: 'https://gl.test/g/p/-/merge_requests/7', related: 4 }]);
  assert.equal(s.gate.decision, 'defer', 'the newest child decides the gate');
  assert.equal(s.rollout.id, 12, 'the newest child that deployed is the rollout');
  assert.equal(s.rollout.jobs['canary-ring'].status, 'running');
  await cs(); assert.equal(calls, 9);
  t = 31_000; await cs(); assert.equal(calls, 18);
  assert.deepEqual(apiParts(url), { api: 'https://gl.test/api/v4', project: '42' });
});

test('html escapes claim data', () => {
  const out = html({ generated_at: 'now', revision: '<x>', node_version: 'v', schema_version: null, invalid_indexes: [],
    trust_policy: { loaded: true }, claims: [{ id: '<script>', state: 'VALID', mr_urls: [], evidence: [{ label: '<b>', value: '<i>', detail: 'x', ok: true }] }], claims_error: null, sci: null });
  assert.doesNotMatch(out, /<script>/);
  assert.match(out, /&lt;script&gt;/);
});

test('live evidence: security floor, distrusted roots, migration rows', async () => {
  const { securityFloor, anchorEvidence, migrationEvidence } = require('../src/live-evidence');
  const rel = [{ version: 'v24.22.1', security: true }, { version: 'v24.22.0', security: false },
               { version: 'v24.18.1', security: true }, { version: 'v22.23.4', security: true }];
  assert.equal(securityFloor(rel, 'v24.22.0'), 'v24.22.1');
  assert.equal(securityFloor(rel, 'v26.1.0'), null);
  const rr = { node_version: 'v24.22.0', effective_ca_spki: ['a'.repeat(64), 'b'.repeat(64)],
               effective_ca_subjects: { ['b'.repeat(64)]: 'CN=Demo Root G3' }, trust_policy: { removed: 0 } };
  const ev = anchorEvidence(rr, { releases: rel, roots: { distrusted: [{ spki_sha256: 'B'.repeat(64) }] } });
  assert.equal(ev[0].ok, false);
  assert.equal(ev[1].ok, false);
  assert.match(ev[1].value, /Demo Root G3/);
  const q = async text => (text.includes('reltuples') ? { rows: [{ n: '850004' }] } : { rows: [{ valid: false }] });
  const m = await migrationEvidence(q, 500000);
  assert.equal(m[0].value, '850,004');
  assert.equal(m[1].ok, false);
});

test('status html: claim history, rollout track, gate and rollback', () => {
  const base = { generated_at: '2026-10-26T18:00:00Z', revision: 'r', node_version: 'v24.22.1', schema_version: null, invalid_indexes: [],
    trust_policy: { loaded: true }, claims_error: null, sci: null, project_url: 'https://gl.test/g/p',
    world: [{ source: 'Root-program distrust feed', fact: 'CN=Demo Root G3 distrusted', effect: 'excluded', ok: true }],
    activity: [{ iid: 4, claim: 'anchor-trusted', state: 'opened', stage: 'reproving', created_at: '2026-10-26T17:09:00Z', web_url: 'https://gl.test/g/p/-/issues/4' }],
    remediation_mrs: [{ iid: 7, web_url: 'https://gl.test/g/p/-/merge_requests/7', related: 4 }],
    gate: { decision: 'defer', created_at: '2026-10-26T17:19:00Z', web_url: 'https://gl.test/p/11' },
    claims: [{ id: 'anchor-trusted', state: 'REPROVING', open_issue_iid: 4, open_issue_url: 'https://gl.test/g/p/-/issues/4', issue_iid: 2,
      mr_urls: [], evidence: [] }] };
  const running = html({ ...base, rollout: { id: 12, status: 'running', web_url: 'u', created_at: '2026-10-26T17:30:00Z',
    jobs: { 'deploy-canary': { status: 'success' }, 'canary-reprove': { status: 'success' }, 'canary-ring': { status: 'running' } } } });
  assert.match(running, /What changed in the world/);
  assert.match(running, />DEFER</);
  assert.match(running, /merge_requests\/7">!7</, 'open MR linked from the claim history');
  assert.match(running, /class="done"><b>Issue/);
  assert.match(running, /class="now"><b>Release/);
  assert.match(running, /canary 50%/);
  assert.match(running, /class="now"><b>50% → 100%/);
  const back = html({ ...base, rollout: { id: 13, status: 'failed', web_url: 'u', created_at: '2026-10-26T17:30:00Z',
    jobs: { 'deploy-canary': { status: 'success' }, 'canary-ring': { status: 'failed' }, rollback: { status: 'success', web_url: 'rb' } } } });
  assert.match(back, /stable 100%/);
  assert.match(back, /restored 100% of traffic/);
  const done = html({ ...base, gate: null, remediation_mrs: [], activity: [], rollout: { id: 14, status: 'success', web_url: 'u', created_at: 'x',
    jobs: { promote: { status: 'success' }, 'canary-ring': { status: 'success' }, mint: { status: 'success' } } },
    claims: [{ id: 'anchor-trusted', state: 'VALID', issue_iid: 4, issue_url: 'https://gl.test/g/p/-/issues/4', minted_at: '2026-10-26T18:00:00Z',
      mr_urls: ['https://gl.test/g/p/-/merge_requests/7'], pipeline_url: 'https://gl.test/p/14', evidence: [] }] });
  assert.match(done, /new stable 100%/);
  assert.equal((done.match(/class="done"><b>(Issue|Duo fix|Release|Minted)/g) || []).length, 4);
});
