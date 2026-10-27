// partner-sim: a fake partner webhook endpoint served over TLS (SRS §13.4). DEMO ONLY.
// :8443 serves a leaf chained to Demo Trusted Root; :8444 serves a leaf chained to Demo Root G3.
'use strict';
const https = require('node:https');
const fs = require('node:fs');
const path = require('node:path');

const CERTS = process.env.PARTNER_CERTS_DIR || path.join(__dirname, 'certs');
const listeners = [
  { port: Number(process.env.TRUSTED_PORT || 8443), name: 'leaf-trusted' },
  { port: Number(process.env.G3_PORT || 8444), name: 'leaf-g3' },
];

for (const { port, name } of listeners) {
  const server = https.createServer({
    cert: fs.readFileSync(path.join(CERTS, `${name}.pem`)),
    key: fs.readFileSync(path.join(CERTS, `${name}.key`)),
  }, (req, res) => {
    req.resume();
    req.on('end', () => {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ ok: true, partner: name }));
    });
  });
  // No host: dual-stack, so `localhost` resolving to ::1 or 127.0.0.1 both connect at once.
  server.listen(port, () => console.log(`partner-sim ${name} listening on :${port}`));
}
