import pytest

from stale_engine.migrate import lockmodes, runner

from .conftest import BASELINE_PENDING, REMEDIATED_PENDING


def write(tmp_path, name, sql):
    p = tmp_path / name
    p.write_text(sql)
    return p


def test_parse_transactional(tmp_path):
    mf = runner.parse_file(write(tmp_path, "20261021090000_add_idx.sql", BASELINE_PENDING))
    assert (mf.version, mf.name, mf.no_transaction) == ("20261021090000", "add_idx", False)
    assert mf.statements() == ["CREATE INDEX ledger_entries_merchant_idx ON ledger_entries(merchant_id)"]
    assert mf.down_sql == "DROP INDEX ledger_entries_merchant_idx;"
    assert mf.checksum.startswith("sha256:")


def test_parse_no_transaction_header(tmp_path):
    mf = runner.parse_file(write(tmp_path, "20261021090000_add_idx.sql", REMEDIATED_PENDING))
    assert mf.no_transaction
    assert len(mf.statements()) == 3


def test_header_must_be_first_line(tmp_path):
    sql = "-- migrate:up\n-- stale:no-transaction\nSELECT 1;\n"
    assert not runner.parse_file(write(tmp_path, "20261021090000_x.sql", sql)).no_transaction


def test_crlf_files(tmp_path):
    p = tmp_path / "20261021090000_x.sql"
    p.write_bytes(REMEDIATED_PENDING.replace("\n", "\r\n").encode())
    assert runner.parse_file(p).no_transaction


@pytest.mark.parametrize("name", ["2026_x.sql", "20261021090000-x.sql", "20261021090000_X.sql"])
def test_bad_filenames(tmp_path, name):
    with pytest.raises(runner.MigrationError):
        runner.parse_file(write(tmp_path, name, BASELINE_PENDING))


def test_missing_up_section(tmp_path):
    with pytest.raises(runner.MigrationError):
        runner.parse_file(write(tmp_path, "20261021090000_x.sql", "CREATE TABLE a(b int);"))


def test_load_dir_orders_and_pending(tmp_path):
    write(tmp_path, "20261021090000_b.sql", BASELINE_PENDING)
    write(tmp_path, "20261020090000_a.sql", "-- migrate:up\nCREATE TABLE t(id int);\n")
    files = runner.load_dir(tmp_path)
    assert [f.version for f in files] == ["20261020090000", "20261021090000"]
    assert [f.version for f in runner.pending(files, {"20261020090000"})] == ["20261021090000"]


def test_describe_relative_path(tmp_path):
    p = write(tmp_path, "20261021090000_b.sql", BASELINE_PENDING)
    d = runner.parse_file(p).describe(rel_to=tmp_path)
    assert d["file"] == "20261021090000_b.sql"
    assert d["statements"][0]["lock_mode"] == lockmodes.SHARE


def test_classify_rejects_multiple_statements():
    with pytest.raises(ValueError):
        lockmodes.classify("SELECT 1; SELECT 2")
