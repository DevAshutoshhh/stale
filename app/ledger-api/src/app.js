'use strict';
const express = require('express');
const balance = require('./routes/balance');
const health = require('./routes/health');
const runtime = require('./routes/runtime');
const status = require('./routes/status');
const webhooks = require('./routes/webhooks');
const { createClaimsStatus } = require('./claims-status');
const { createLiveEvidence } = require('./live-evidence');

function createApp(deps = {}) {
  const {
    query = require('./db').query,
    tokens = balance.parseTokens(process.env.DEMO_TOKENS || 'alpha:tok-alpha,beta:tok-beta'),
    partners = { trusted: process.env.PARTNER_TRUSTED_URL || 'https://localhost:8443/hook',
                 distrusted: process.env.PARTNER_DISTRUSTED_URL || 'https://localhost:8444/hook' },
    claimsStatus = createClaimsStatus(),
    liveFeeds = createLiveEvidence(),
    threshold = Number(process.env.STALE_ROW_THRESHOLD || 500000),
    revision = () => process.env.K_REVISION || 'local',
  } = deps;

  const app = express();
  app.disable('x-powered-by');
  app.use(express.json({ limit: '16kb' }));
  app.use(health());
  app.use(runtime());
  app.use(balance({ query, tokens, revision }));
  app.use(webhooks({ partners }));
  app.use(status({ query, claimsStatus, liveFeeds, threshold }));
  app.get('/', (req, res) => res.redirect(302, '/status'));
  app.use((err, req, res, next) => { // eslint-disable-line no-unused-vars
    console.error(err);
    res.status(500).json({ error: 'internal error' });
  });
  return app;
}

module.exports = { createApp };
