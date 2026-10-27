'use strict';
const { Pool } = require('pg');

let pool = null;

function getPool() {
  if (!pool) {
    if (!process.env.DATABASE_URL) throw new Error('DATABASE_URL is not set');
    pool = new Pool({ connectionString: process.env.DATABASE_URL, max: 5, connectionTimeoutMillis: 5000 });
    pool.on('error', err => console.error('pg pool error:', err.message));
  }
  return pool;
}

async function query(text, params) {
  return getPool().query(text, params);
}

module.exports = { query };
