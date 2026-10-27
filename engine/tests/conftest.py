from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

REPO = Path(__file__).resolve().parents[2]
CLAIMS_DIR = REPO / ".stale" / "claims"

TRUSTED_SPKI = "a" * 64
G3_SPKI = "3eab3fba" + "0" * 56
RSA_SPKI = "b" * 64


def ec_x(k: int, curve=ec.SECP256R1()) -> str:
    return f"{ec.derive_private_key(k, curve).public_key().public_numbers().x:064x}"


def random_ec_x() -> str:
    return f"{ec.generate_private_key(ec.SECP256R1()).public_key().public_numbers().x:064x}"


@pytest.fixture
def runtime_report() -> dict:
    """A healthy baseline report: Node at the floor, one random-key EC root, one 4096-bit RSA root."""
    return {
        "node_version": "v24.22.1",
        "revision": "ledger-api-00012-xyz",
        "trust_policy": {"loaded": True, "excluded_spki_count": 0, "removed": 0},
        "ca_source_flags": {"use_system_ca": False, "use_openssl_ca": False, "extra_ca_file": True},
        "effective_ca_spki": [TRUSTED_SPKI, RSA_SPKI],
        "effective_ca_subjects": {TRUSTED_SPKI: "CN=Demo Trusted Root", RSA_SPKI: "CN=Some RSA Root"},
        "effective_ca_keys": {
            TRUSTED_SPKI: {"type": "ec", "curve": "prime256v1", "x": random_ec_x()},
            RSA_SPKI: {"type": "rsa", "bits": 4096},
        },
    }


@pytest.fixture
def anchor_evidence(runtime_report) -> dict:
    return {
        "runtime_report": runtime_report,
        "node_feed": {"line": "v24", "line_security_floor": "v24.22.1", "line_latest": "v24.22.1",
                      "line_eol": "2028-04-30"},
        "root_feed": {"updated_at": "2026-10-24T00:00:00Z", "distrusted_spki": [], "distrusted": []},
    }


@pytest.fixture
def g3_evidence(anchor_evidence) -> dict:
    """Baseline + Demo Root G3 (private scalar 4) in the effective set."""
    ev = copy.deepcopy(anchor_evidence)
    rr = ev["runtime_report"]
    rr["effective_ca_spki"].append(G3_SPKI)
    rr["effective_ca_subjects"][G3_SPKI] = "CN=Demo Root G3"
    rr["effective_ca_keys"][G3_SPKI] = {"type": "ec", "curve": "prime256v1", "x": ec_x(4)}
    return ev


@pytest.fixture
def anchor_claim():
    import yaml

    from stale_engine.models import Claim

    return Claim.model_validate(yaml.safe_load((CLAIMS_DIR / "anchor-trusted.yaml").read_text()))


@pytest.fixture
def migration_claim():
    import yaml

    from stale_engine.models import Claim

    return Claim.model_validate(yaml.safe_load((CLAIMS_DIR / "migration-safe.yaml").read_text()))


BASELINE_PENDING = """-- migrate:up
CREATE INDEX ledger_entries_merchant_idx ON ledger_entries(merchant_id);
-- migrate:down
DROP INDEX ledger_entries_merchant_idx;
"""

REMEDIATED_PENDING = """-- stale:no-transaction
-- migrate:up
SET lock_timeout = '1500ms';
DROP INDEX CONCURRENTLY IF EXISTS ledger_entries_merchant_idx;
CREATE INDEX CONCURRENTLY ledger_entries_merchant_idx ON ledger_entries(merchant_id);
-- migrate:down
DROP INDEX CONCURRENTLY IF EXISTS ledger_entries_merchant_idx;
"""


def pending_from_sql(tmp_path: Path, sql: str, name: str = "20261021090000_add_ledger_entries_merchant_idx.sql"):
    from stale_engine.migrate import runner

    p = tmp_path / name
    p.write_text(sql)
    return [runner.parse_file(p).describe()]


def migration_evidence(tmp_path: Path, sql: str, rows: int, threshold: int = 500_000) -> dict:
    return {"pending_migrations": pending_from_sql(tmp_path, sql),
            "pg_stats": {"ledger_entries": {"reltuples": rows, "relpages": rows // 100}},
            "threshold": threshold}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("STALE_", "CI_", "DB_", "GCP_")) or k == "DATABASE_URL":
            monkeypatch.delenv(k, raising=False)
