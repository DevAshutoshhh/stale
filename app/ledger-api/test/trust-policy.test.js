'use strict';
// End-to-end: trust policy + real TLS handshakes against partner-sim on loopback.
const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { spawn, execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');

const APP = path.resolve(__dirname, '..');
const ROOTS = path.join(APP, 'deploy/trust/extra-roots.pem');
const SIM = path.resolve(APP, '../partner-sim/server.js');
const PORTS = { trusted: 18443, g3: 18444 };

function spkiOf(pem) {
  return crypto.createHash('sha256')
    .update(new crypto.X509Certificate(pem).publicKey.export({ type: 'spki', format: 'der' })).digest('hex');
}
const pems = fs.readFileSync(ROOTS, 'utf8').match(/-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----/g);
const [TRUSTED_SPKI, G3_SPKI] = pems.map(spkiOf);

function runWithPolicy(excluded, script) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'stale-'));
  const file = path.join(dir, 'excluded-spki.txt');
  fs.writeFileSync(file, excluded.join('\n'));
  const out = execFileSync(process.execPath, ['--require', './src/trust-policy.cjs', ...script], {
    cwd: APP, encoding: 'utf8',
    env: { ...process.env, NODE_EXTRA_CA_CERTS: ROOTS, STALE_EXCLUDED_SPKI_FILE: file, NODE_OPTIONS: '' },
  });
  return JSON.parse(out);
}

const DELIVER = `
const { deliver } = require('./src/routes/webhooks');
Promise.all([deliver('https://localhost:${PORTS.trusted}/hook', {}), deliver('https://localhost:${PORTS.g3}/hook', {})])
  .then(([t, d]) => console.log(JSON.stringify({ t, d })));`;

let sim;
before(async () => {
  sim = spawn(process.execPath, [SIM], { env: { ...process.env, TRUSTED_PORT: PORTS.trusted, G3_PORT: PORTS.g3 } });
  await new Promise((resolve, reject) => {
    let seen = 0;
    sim.stdout.on('data', d => { seen += (String(d).match(/listening/g) || []).length; if (seen >= 2) resolve(); });
    sim.on('exit', code => reject(new Error(`partner-sim exited ${code}`)));
  });
});
after(() => sim && sim.kill());

test('baseline: both demo roots are in the effective set, nothing removed', () => {
  const r = runWithPolicy([], ['src/runtime-report.cjs']);
  assert.deepEqual(r.trust_policy, { loaded: true, excluded_spki_count: 0, removed: 0 });
  assert.ok(r.effective_ca_spki.includes(TRUSTED_SPKI));
  assert.ok(r.effective_ca_spki.includes(G3_SPKI));
  assert.equal(r.effective_ca_subjects[G3_SPKI].includes('CN=Demo Root G3'), true);
  assert.equal(r.effective_ca_keys[G3_SPKI].type, 'ec');
  assert.equal(r.effective_ca_keys[G3_SPKI].curve, 'prime256v1');
  assert.ok(r.effective_ca_count > 100, 'bundled Mozilla roots are still there');
  assert.equal(r.ca_source_flags.extra_ca_file, true);
});

test('excluding G3 removes exactly one root and keeps the rest', () => {
  const r = runWithPolicy([G3_SPKI.toUpperCase(), 'not-a-hash'], ['src/runtime-report.cjs']);
  assert.deepEqual(r.trust_policy, { loaded: true, excluded_spki_count: 1, removed: 1 });
  assert.ok(!r.effective_ca_spki.includes(G3_SPKI));
  assert.ok(r.effective_ca_spki.includes(TRUSTED_SPKI));
});

test('TLS at baseline: both partners deliver', () => {
  const r = runWithPolicy([], ['-e', DELIVER]);
  assert.equal(r.t.ok, true);
  assert.equal(r.d.ok, true);
});

test('TLS with G3 excluded: trusted delivers, distrusted fails with a certificate error', () => {
  const r = runWithPolicy([G3_SPKI], ['-e', DELIVER]);
  assert.equal(r.t.ok, true);
  assert.equal(r.d.ok, false);
  assert.ok(['SELF_SIGNED_CERT_IN_CHAIN', 'UNABLE_TO_GET_ISSUER_CERT_LOCALLY',
    'UNABLE_TO_VERIFY_LEAF_SIGNATURE', 'CERT_UNTRUSTED'].includes(r.d.code), r.d.code);
});
