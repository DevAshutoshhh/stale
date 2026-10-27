-- Local docker-compose only: the two demo users (SRS §14) plus some background rows.
TRUNCATE ledger_entries;
INSERT INTO ledger_entries(user_id, merchant_id, amount_cents) VALUES
  ('alpha', 'm-0001', 6000), ('alpha', 'm-0002', 4000),
  ('beta',  'm-0001', 20000), ('beta', 'm-0003', 5000);
INSERT INTO ledger_entries(user_id, merchant_id, amount_cents)
  SELECT 'u-' || (g % 5000), 'm-' || lpad((g % 1000)::text, 4, '0'), (g % 997) + 1
  FROM generate_series(1, 1000) AS g;
ANALYZE ledger_entries;
