"""Tests against a real PostgreSQL. Run with STALE_TEST_DATABASE_URL pointing at a database whose
name starts with "shadow" (it is wiped). Skipped otherwise. CI uses a postgres:18 service."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from stale_engine.migrate import runner
from stale_engine.reproof import shadow_migration

from .conftest import BASELINE_PENDING, REMEDIATED_PENDING, REPO

URL = os.environ.get("STALE_TEST_DATABASE_URL")
MIGRATIONS = REPO / "app/ledger-api/db/migrations"
pytestmark = [pytest.mark.postgres, pytest.mark.skipif(not URL, reason="STALE_TEST_DATABASE_URL not set")]
INIT = "20261020090000"


@pytest.fixture(autouse=True)
def _url(monkeypatch):
    # conftest clears STALE_* env vars; restore the one we need
    monkeypatch.setenv("STALE_TEST_DATABASE_URL", URL or "")


def fresh():
    import psycopg

    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS public CASCADE")
        c.execute("CREATE SCHEMA public")


def test_apply_records_versions_and_is_idempotent():
    fresh()
    assert runner.apply(URL, MIGRATIONS, log=lambda m: None) == [INIT, "20261021090000"]
    assert runner.apply(URL, MIGRATIONS, log=lambda m: None) == []


def test_no_transaction_file_runs_in_autocommit(tmp_path):
    fresh()
    shutil.copy(MIGRATIONS / f"{INIT}_init_ledger.sql", tmp_path)
    (tmp_path / "20261021090000_add_ledger_entries_merchant_idx.sql").write_text(REMEDIATED_PENDING)
    assert runner.apply(URL, tmp_path, log=lambda m: None) == [INIT, "20261021090000"]


def test_concurrent_in_transaction_fails_and_is_not_recorded(tmp_path):
    fresh()
    shutil.copy(MIGRATIONS / f"{INIT}_init_ledger.sql", tmp_path)
    (tmp_path / "20261021090000_x.sql").write_text(REMEDIATED_PENDING.replace("-- stale:no-transaction\n", ""))
    with pytest.raises(runner.MigrationError, match="ActiveSqlTransaction|transaction block"):
        runner.apply(URL, tmp_path, log=lambda m: None)
    import psycopg

    with psycopg.connect(URL) as c:
        assert runner.applied_versions(c) == {INIT}


def test_interrupted_concurrent_build_leaves_invalid_index_detected(tmp_path):
    """A CIC that fails mid-build leaves an INVALID index; the runner must detect it and not record."""
    fresh()
    shutil.copy(MIGRATIONS / f"{INIT}_init_ledger.sql", tmp_path)
    runner.apply(URL, tmp_path, log=lambda m: None)
    import psycopg

    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("INSERT INTO ledger_entries(user_id, merchant_id, amount_cents) "
                  "SELECT 'u', 'm-dup', 1 FROM generate_series(1, 1000)")
    # UNIQUE over duplicate data: the concurrent build fails after creating the catalog entry.
    (tmp_path / "20261021090000_uniq.sql").write_text(
        "-- stale:no-transaction\n-- migrate:up\n"
        "CREATE UNIQUE INDEX CONCURRENTLY ledger_entries_merchant_uq ON ledger_entries(merchant_id);\n")
    with pytest.raises(runner.MigrationError) as e:
        runner.apply(URL, tmp_path, log=lambda m: None)
    assert "INVALID index(es) left behind: ledger_entries_merchant_uq" in str(e.value)
    with psycopg.connect(URL) as c:
        assert runner.invalid_indexes(c) == ["ledger_entries_merchant_uq"]
        assert runner.applied_versions(c) == {INIT}


def test_shadow_guard():
    with pytest.raises(shadow_migration.ShadowGuardError):
        shadow_migration.run("postgresql://u@h/ledger", MIGRATIONS, threshold=1)


def test_shadow_blocking_index_fails(tmp_path):
    # Uses its own copy of the blocking migration: the repo file may already be the remediated one.
    d = tmp_path / "m"
    d.mkdir()
    shutil.copy(MIGRATIONS / f"{INIT}_init_ledger.sql", d)
    (d / "20261021090000_add_ledger_entries_merchant_idx.sql").write_text(BASELINE_PENDING)
    rep = shadow_migration.run(URL, d, threshold=500_000, applied_versions={INIT},
                               out_dir=tmp_path, log=lambda m: None)
    assert not rep["passed"]
    assert rep["blocking_spans_ms"].get("ShareLock", 0) > shadow_migration.MAX_BLOCKING_MS
    assert (tmp_path / "shadow-report.json").exists()


def test_shadow_concurrent_rewrite_passes(tmp_path):
    d = tmp_path / "m"
    d.mkdir()
    shutil.copy(MIGRATIONS / f"{INIT}_init_ledger.sql", d)
    (d / "20261021090000_add_ledger_entries_merchant_idx.sql").write_text(REMEDIATED_PENDING)
    rep = shadow_migration.run(URL, d, threshold=500_000, applied_versions={INIT}, out_dir=tmp_path,
                               log=lambda m: None)
    assert rep["passed"], rep["failures"]
    assert "ShareLock" not in rep["blocking_spans_ms"]


def test_blocking_spans():
    tl = [{"t_ms": 0, "modes": []}, {"t_ms": 50, "modes": ["ShareLock"]}, {"t_ms": 100, "modes": ["ShareLock"]},
          {"t_ms": 150, "modes": []}, {"t_ms": 200, "modes": ["ShareLock"]}]
    assert shadow_migration.blocking_spans(tl) == {"ShareLock": 100}
