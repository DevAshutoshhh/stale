'use strict';
const { Router } = require('express');

function parseTokens(spec) {
  const map = new Map();
  for (const pair of (spec || '').split(',')) {
    const [user, token] = pair.split(':').map(s => (s || '').trim());
    if (user && token) map.set(token, user);
  }
  return map;
}

module.exports = ({ query, tokens, revision }) => Router().get('/api/v1/balance', async (req, res, next) => {
  res.set('Cache-Control', 'private, no-store').set('Vary', 'Authorization');
  const m = /^Bearer (\S+)$/.exec(req.get('authorization') || '');
  const user = m && tokens.get(m[1]);
  if (!user) return res.status(401).json({ error: 'missing or unknown bearer token' });
  try {
    const { rows } = await query(
      'SELECT COALESCE(SUM(amount_cents),0)::bigint AS balance FROM ledger_entries WHERE user_id=$1', [user]);
    res.json({ user_id: user, balance_cents: Number(rows[0].balance), revision: revision() });
  } catch (err) {
    next(err);
  }
});

module.exports.parseTokens = parseTokens;
