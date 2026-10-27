-- stale:no-transaction
-- migrate:up
SET lock_timeout = '1500ms';
DROP INDEX CONCURRENTLY IF EXISTS ledger_entries_merchant_idx;
CREATE INDEX CONCURRENTLY ledger_entries_merchant_idx ON ledger_entries(merchant_id);
-- migrate:down
DROP INDEX CONCURRENTLY IF EXISTS ledger_entries_merchant_idx;
