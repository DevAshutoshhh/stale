'use strict';
const https = require('node:https');
const { Router } = require('express');

// Uses the DEFAULT agent and no `ca:` option, so the trust policy's default CA set applies (§13.3).
function deliver(url, payload) {
  return new Promise(resolve => {
    const body = JSON.stringify(payload);
    const req = https.request(url, {
      method: 'POST', servername: 'localhost', timeout: 3000,
      headers: { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body) },
    }, res => {
      res.resume();
      res.on('end', () => resolve({ ok: res.statusCode < 300, status: res.statusCode }));
    });
    req.on('timeout', () => req.destroy(Object.assign(new Error('timeout'), { code: 'ETIMEDOUT' })));
    req.on('error', err => resolve({ ok: false, code: err.code || 'UNKNOWN', message: err.message }));
    req.end(body);
  });
}

module.exports = ({ partners }) => Router().post('/api/v1/webhooks/deliver', async (req, res) => {
  const target = req.body && req.body.target;
  const url = partners[target];
  if (!url) return res.status(400).json({ delivered: false, error: 'target must be trusted or distrusted' });
  const r = await deliver(url, { event: 'ledger.settled', at: new Date().toISOString() });
  if (r.ok) return res.json({ delivered: true, target, partner_status: r.status });
  return res.status(502).json({ delivered: false, target, tls_error: r.code || `HTTP_${r.status}`,
                                message: r.message });
});

module.exports.deliver = deliver;
