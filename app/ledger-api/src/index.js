'use strict';
// Start with: node --require ./src/trust-policy.cjs src/index.js
const { createApp } = require('./app');

if (!globalThis.__stalePolicy) {
  console.warn('WARNING: trust policy not loaded; start with --require ./src/trust-policy.cjs');
}
const port = Number(process.env.PORT || 8080);
createApp().listen(port, () => {
  const p = globalThis.__stalePolicy || {};
  console.log(`ledger-api ${process.version} on :${port} revision=${process.env.K_REVISION || 'local'} ` +
              `trust-policy removed=${p.removed ?? 'n/a'}`);
});
