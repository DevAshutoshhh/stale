import pytest
import yaml
from pydantic import ValidationError

from stale_engine import config
from stale_engine.models import Claim, Lockfile

from .conftest import CLAIMS_DIR, REPO


def load(name):
    return yaml.safe_load((CLAIMS_DIR / name).read_text())


def test_claims_validate():
    for f in CLAIMS_DIR.glob("*.yaml"):
        c = Claim.model_validate(load(f.name))
        assert c.id == f.stem


def test_unknown_key_rejected():
    data = load("anchor-trusted.yaml")
    data["spec"]["surprise"] = 1
    with pytest.raises(ValidationError):
        Claim.model_validate(data)


@pytest.mark.parametrize("mutate", [
    lambda d: d["metadata"].update(id="Bad_Id"),
    lambda d: d["spec"].update(rules=["R9"]),
    lambda d: d["spec"]["evidence"].append({"collector": "nope"}),
    lambda d: d.update(apiVersion="stale/v1"),
    lambda d: d["spec"]["remediation"].update(allowPaths=[]),
])
def test_invalid_claims(mutate):
    data = load("anchor-trusted.yaml")
    mutate(data)
    with pytest.raises(ValidationError):
        Claim.model_validate(data)


def test_lockfile_entries_are_unminted_or_well_formed():
    """The seed has nulls; after `stale mint` an entry carries a full proof. Nothing in between."""
    lock = Lockfile.model_validate_json((REPO / ".stale" / "claims.lock.json").read_text())
    assert set(lock.claims) == {"anchor-trusted", "migration-safe"}
    for e in lock.claims.values():
        if e.minted_at is None:
            assert e.fingerprint is None and e.revision is None and e.mr_iids == []
        else:
            assert e.fingerprint and e.fingerprint.startswith("sha256:") and len(e.fingerprint) == 71
            assert e.revision and e.commit_sha
    assert lock.sci.reproofs_skipped >= 0 and lock.sci.gco2eq_avoided == 0.0


def test_require_fails_fast_listing_all_missing():
    with pytest.raises(config.ConfigError) as e:
        config.require("STALE_BOT_TOKEN", "CI_PROJECT_ID", "GCP_REGION")
    assert "CI_PROJECT_ID, STALE_BOT_TOKEN" in str(e.value)
    assert "GCP_REGION" not in str(e.value)  # has a default


def test_unregistered_variable_is_a_bug():
    with pytest.raises(KeyError):
        config.get("NOT_IN_REGISTRY")


def test_expand(monkeypatch):
    monkeypatch.setenv("STALE_FEEDS_BASE_URL", "https://x/feeds")
    assert config.expand("${STALE_FEEDS_BASE_URL}/a.json") == "https://x/feeds/a.json"
    monkeypatch.delenv("STALE_FEEDS_BASE_URL")
    with pytest.raises(config.ConfigError):
        config.expand("${STALE_FEEDS_BASE_URL}/a.json")


def test_database_url(monkeypatch):
    with pytest.raises(config.ConfigError):
        config.database_url()
    monkeypatch.setenv("DB_PASSWORD", "p@ss/word")
    # Fake test password, checks URL-encoding only.
    assert config.database_url() == "postgresql://stale_migrator:p%40ss%2Fword@127.0.0.1:5432/ledger"  # gitleaks:allow
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    assert config.database_url() == "postgresql://u@h/d"
