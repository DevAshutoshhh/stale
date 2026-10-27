import pytest

from stale_engine import config, rules
from stale_engine.rules import migration

from .conftest import BASELINE_PENDING, REMEDIATED_PENDING, migration_evidence

ALL = {"M1", "M3", "M4", "M5"}


def ids(findings):
    return sorted({f.rule for f in findings})


def test_small_table_plain_index_passes(tmp_path):
    ev = migration_evidence(tmp_path, BASELINE_PENDING, rows=100_000)
    assert migration.evaluate(ev, ALL) == []


def test_large_table_plain_index_fires_m1_m3(tmp_path):
    ev = migration_evidence(tmp_path, BASELINE_PENDING, rows=750_000)
    f = migration.evaluate(ev, ALL)
    assert ids(f) == ["M1", "M3"]
    m1 = next(x for x in f if x.rule == "M1")
    assert m1.detail["table"] == "ledger_entries"
    assert m1.detail["index_name"] == "ledger_entries_merchant_idx"
    assert m1.detail["lock_mode"] == "SHARE"


def test_threshold_boundary(tmp_path):
    assert ids(migration.evaluate(migration_evidence(tmp_path, BASELINE_PENDING, 500_000), ALL)) == ["M1", "M3"]
    assert migration.evaluate(migration_evidence(tmp_path, BASELINE_PENDING, 499_999), ALL) == []


def test_threshold_defaults_to_config(tmp_path, monkeypatch):
    monkeypatch.setenv("STALE_ROW_THRESHOLD", "1000")
    ev = migration_evidence(tmp_path, BASELINE_PENDING, rows=2000)
    del ev["threshold"]
    assert config.row_threshold() == 1000
    assert ids(migration.evaluate(ev, ALL)) == ["M1", "M3"]


def test_remediated_shape_passes_on_large_table(tmp_path):
    ev = migration_evidence(tmp_path, REMEDIATED_PENDING, rows=900_000)
    assert migration.evaluate(ev, ALL) == []


def test_m4_concurrent_without_header(tmp_path):
    sql = REMEDIATED_PENDING.replace("-- stale:no-transaction\n", "")
    assert ids(migration.evaluate(migration_evidence(tmp_path, sql, rows=10), ALL)) == ["M4"]


def test_m5_if_not_exists(tmp_path):
    sql = ("-- stale:no-transaction\n-- migrate:up\n"
           "CREATE INDEX CONCURRENTLY IF NOT EXISTS ledger_entries_merchant_idx ON ledger_entries(merchant_id);\n")
    f = migration.evaluate(migration_evidence(tmp_path, sql, rows=10), ALL)
    assert ids(f) == ["M5"] and "IF NOT EXISTS" in f[0].detail["reason"]


def test_m5_missing_preceding_drop(tmp_path):
    sql = ("-- stale:no-transaction\n-- migrate:up\nSET lock_timeout = '1s';\n"
           "CREATE INDEX CONCURRENTLY ledger_entries_merchant_idx ON ledger_entries(merchant_id);\n")
    f = migration.evaluate(migration_evidence(tmp_path, sql, rows=10), ALL)
    assert ids(f) == ["M5"] and "DROP INDEX CONCURRENTLY" in f[0].detail["reason"]


def test_m5_drop_of_other_index_does_not_count(tmp_path):
    sql = REMEDIATED_PENDING.replace("IF EXISTS ledger_entries_merchant_idx;\nCREATE",
                                     "IF EXISTS some_other_idx;\nCREATE")
    assert ids(migration.evaluate(migration_evidence(tmp_path, sql, rows=10), ALL)) == ["M5"]


@pytest.mark.parametrize("timeout,fires", [("'1500ms'", False), ("'2s'", False), ("2000", False),
                                           ("'3s'", True), ("0", True)])
def test_m3_lock_timeout_limit(tmp_path, timeout, fires):
    sql = (f"-- migrate:up\nSET lock_timeout = {timeout};\n"
           "ALTER TABLE ledger_entries ADD COLUMN note text;\n")
    f = migration.evaluate(migration_evidence(tmp_path, sql, rows=900_000), ALL)
    assert (ids(f) == ["M3"]) is fires


def test_m3_reset_lock_timeout_clears_it(tmp_path):
    sql = ("-- migrate:up\nSET lock_timeout = '1s';\nRESET lock_timeout;\n"
           "ALTER TABLE ledger_entries ADD COLUMN note text;\n")
    assert ids(migration.evaluate(migration_evidence(tmp_path, sql, rows=900_000), ALL)) == ["M3"]


def test_unknown_statement_is_access_exclusive(tmp_path):
    sql = "-- migrate:up\nLOCK TABLE ledger_entries;\n"
    # LOCK TABLE is unknown to the table → fail-safe ACCESS EXCLUSIVE, but its table is unknown too
    ev = migration_evidence(tmp_path, sql, rows=900_000)
    assert ev["pending_migrations"][0]["statements"][0]["lock_mode"] == "ACCESS EXCLUSIVE"


def test_unknown_table_is_not_large(tmp_path):
    ev = migration_evidence(tmp_path, BASELINE_PENDING.replace("ON ledger_entries", "ON other_table"),
                            rows=900_000)
    assert migration.evaluate(ev, ALL) == []


def test_only_listed_rules_run(tmp_path):
    ev = migration_evidence(tmp_path, BASELINE_PENDING, rows=900_000)
    assert ids(migration.evaluate(ev, {"M1"})) == ["M1"]


def test_no_pending_is_valid():
    assert migration.evaluate({"pending_migrations": [], "pg_stats": {}}, ALL) == []


def test_scenario_b_facts(tmp_path, migration_claim):
    ev = migration_evidence(tmp_path, BASELINE_PENDING, rows=850_000)
    findings = rules.evaluate(migration_claim, ev)
    f = rules.facts(migration_claim, findings)
    assert f["lock_timeout"] == "1500ms"
    assert f["no_transaction_header"] == "-- stale:no-transaction"
    [entry] = f["files"]
    assert entry["rules"] == ["M1", "M3"]
    assert entry["index_name"] == "ledger_entries_merchant_idx"
    assert entry["table"] == "ledger_entries" and entry["reltuples"] == 850_000
    assert entry["version"] == "20261021090000"
