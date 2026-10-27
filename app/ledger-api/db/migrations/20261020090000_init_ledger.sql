-- migrate:up
CREATE TABLE ledger_entries (
  id bigserial PRIMARY KEY,
  user_id text NOT NULL,
  merchant_id text NOT NULL,
  amount_cents bigint NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ledger_entries_user_idx ON ledger_entries(user_id);
-- migrate:down
DROP TABLE ledger_entries;
