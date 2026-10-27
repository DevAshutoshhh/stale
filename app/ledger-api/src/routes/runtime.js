'use strict';
const { Router } = require('express');
const { report } = require('../runtime-report.cjs');

// Unauthenticated: the report holds public CA metadata only, no secrets.
module.exports = () => Router().get('/internal/runtime', (req, res) => {
  res.set('Cache-Control', 'no-store').json(report());
});
