# Playbook: migration-safe

Use this playbook when a claim issue for `migration-safe` is assigned to you. You rewrite the
pending migration file(s) named in `facts` so that they no longer block writes on a large table.

## 1. Read the facts

The issue body has a JSON block between `<!-- stale:machine:begin -->` and
`<!-- stale:machine:end -->`. Its `facts` object contains:

| Key | Meaning | Example |
|---|---|---|
| `files[]` | One entry per pending migration file to rewrite | see below |
| `files[].file` | Repository path of the file | `app/ledger-api/db/migrations/20261021090000_add_ledger_entries_merchant_idx.sql` |
| `files[].index_name` | Index the file creates | `ledger_entries_merchant_idx` |
| `files[].table` | Table the index is on | `ledger_entries` |
| `files[].rules` | Rules that fired (M1 blocking index, M3 no lock_timeout, M4 CONCURRENTLY in a transaction, M5 not retry-safe) | `["M1","M3"]` |
| `lock_timeout` | Value to use for `SET lock_timeout` | `1500ms` |
| `no_transaction_header` | Exact first line the file must start with | `-- stale:no-transaction` |

Use these values exactly. Keep each file's **version and filename unchanged**: rewrite the file in
place. Never create a new migration file and never rename one.

## 2. Edit — the target shape

For each entry in `facts.files`, rewrite the file to exactly this shape (substitute
`<index_name>`, `<table>`, the column list from the original file, and `<lock_timeout>`):

```sql
-- stale:no-transaction
-- migrate:up
SET lock_timeout = '<lock_timeout>';
DROP INDEX CONCURRENTLY IF EXISTS <index_name>;
CREATE INDEX CONCURRENTLY <index_name> ON <table>(<columns>);
-- migrate:down
DROP INDEX CONCURRENTLY IF EXISTS <index_name>;
```

Before (`20261021090000_add_ledger_entries_merchant_idx.sql`):

```sql
-- migrate:up
CREATE INDEX ledger_entries_merchant_idx ON ledger_entries(merchant_id);
-- migrate:down
DROP INDEX ledger_entries_merchant_idx;
```

After:

```sql
-- stale:no-transaction
-- migrate:up
SET lock_timeout = '1500ms';
DROP INDEX CONCURRENTLY IF EXISTS ledger_entries_merchant_idx;
CREATE INDEX CONCURRENTLY ledger_entries_merchant_idx ON ledger_entries(merchant_id);
-- migrate:down
DROP INDEX CONCURRENTLY IF EXISTS ledger_entries_merchant_idx;
```

Why each line matters:

- `-- stale:no-transaction` must be the **first line**: `CONCURRENTLY` cannot run inside a
  transaction, so the runner switches to autocommit (M4).
- `SET lock_timeout` makes any lock wait fail fast instead of queueing writers behind it (M3).
- `DROP INDEX CONCURRENTLY IF EXISTS` first removes an INVALID index left by an earlier failed
  build. Without it the build is not retry-safe (M5).
- `CREATE INDEX CONCURRENTLY` does not block inserts or updates (M1).

## 3. Forbidden edits

- Do **not** use `CREATE INDEX CONCURRENTLY IF NOT EXISTS`. It silently skips a broken INVALID index.
- Do **not** add `BEGIN`/`COMMIT`, and do not change any other migration file, including applied ones.
- Do **not** change the version, the filename, the index name, the table or the columns.
- Do **not** edit anything under `.stale/`, `ci/`, `duo/`, `.gitlab-ci.yml`, or application source.
- Do **not** edit any file outside `allowPaths`: `app/ledger-api/db/migrations/**`.

## 4. Merge request

- Branch: `stale/migration-safe/<issue-iid>-a<attempt>`
- Commit message: `fix(stale): remediate migration-safe`
- Title: `fix(stale): remediate migration-safe (attempt <n>)`
- Labels: `stale-remediation`, `stale-claim::migration-safe` (plus `stale-retry` when attempt > 1)
- Description: `Related to #<issue-iid>`, then one bullet per changed file saying why. Never write a
  closing keyword (close, fix, resolve and their forms) next to an issue reference.

If an analyst note (first line `[stale:analyst]`) exists on the issue, apply its instruction
as long as it stays inside `allowPaths`.
