'use strict';
const { Router } = require('express');

module.exports = () => Router().get('/healthz', (req, res) => res.type('text/plain').send('ok'));
