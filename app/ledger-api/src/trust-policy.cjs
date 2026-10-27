// Runs before any TLS use. Removes distrusted anchors from Node's default CA set.
const tls = require('node:tls'), crypto = require('node:crypto'), fs = require('node:fs');
const file = process.env.STALE_EXCLUDED_SPKI_FILE;
const excluded = new Set((file && fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : '')
  .split('\n').map(s => s.trim().toLowerCase()).filter(s => /^[0-9a-f]{64}$/.test(s)));
const spki = pem => crypto.createHash('sha256')
  .update(new crypto.X509Certificate(pem).publicKey.export({ type: 'spki', format: 'der' }))
  .digest('hex');
const before = tls.getCACertificates('default');
const kept = before.filter(p => !excluded.has(spki(p)));
if (kept.length !== before.length) tls.setDefaultCACertificates(kept);
globalThis.__stalePolicy = { loaded: true, excluded_spki_count: excluded.size,
                             removed: before.length - kept.length };
