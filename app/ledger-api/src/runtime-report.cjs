// Runtime trust report (SRS §9.1). Served at /internal/runtime and printed by
// `node --require ./src/trust-policy.cjs src/runtime-report.cjs` inside a candidate image (§7.2).
'use strict';
const tls = require('node:tls');
const crypto = require('node:crypto');

function flagSet(flag) {
  return process.execArgv.includes(flag) || (process.env.NODE_OPTIONS || '').split(/\s+/).includes(flag);
}

function describeCert(pem) {
  const cert = new crypto.X509Certificate(pem);
  const key = cert.publicKey;
  const der = key.export({ type: 'spki', format: 'der' });
  const spki = crypto.createHash('sha256').update(der).digest('hex');
  const subject = cert.subject.split('\n').filter(Boolean).join(', ');
  let info = { type: key.asymmetricKeyType };
  if (key.asymmetricKeyType === 'ec') {
    const jwk = key.export({ format: 'jwk' });
    info = { type: 'ec', curve: key.asymmetricKeyDetails.namedCurve,
             x: Buffer.from(jwk.x, 'base64url').toString('hex') };
  } else if (key.asymmetricKeyType === 'rsa' || key.asymmetricKeyType === 'rsa-pss') {
    info = { type: 'rsa', bits: key.asymmetricKeyDetails.modulusLength };
  }
  return { spki, subject, key: info };
}

let cached = null;

function report() {
  if (!cached) {
    // The default set does not change after the trust policy ran, so compute it once.
    const certs = tls.getCACertificates('default').map(describeCert);
    const spkis = [...new Set(certs.map(c => c.spki))].sort();
    const subjects = {}, keys = {};
    for (const c of certs) { subjects[c.spki] = c.subject; keys[c.spki] = c.key; }
    cached = {
      trust_policy: globalThis.__stalePolicy || { loaded: false, excluded_spki_count: 0, removed: 0 },
      ca_source_flags: { use_system_ca: flagSet('--use-system-ca'), use_openssl_ca: flagSet('--use-openssl-ca'),
                         extra_ca_file: Boolean(process.env.NODE_EXTRA_CA_CERTS) },
      effective_ca_count: spkis.length,
      effective_ca_spki: spkis,
      effective_ca_subjects: subjects,
      effective_ca_keys: keys,
    };
  }
  return { node_version: process.version, revision: process.env.K_REVISION || 'local', ...cached };
}

module.exports = { report, describeCert };

if (require.main === module) {
  process.stdout.write(JSON.stringify(report(), null, 2) + '\n');
}
