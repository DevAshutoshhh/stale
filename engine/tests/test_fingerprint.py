import copy
import json

from stale_engine.fingerprint import fingerprint, normalise, row_bucket, select

ANCHOR_INCLUDE = ["runtime_report.node_version", "runtime_report.effective_ca_spki",
                  "node_feed.line_security_floor", "node_feed.line_eol", "root_feed.distrusted_spki"]
MIG_INCLUDE = ["pending_migrations", "pg_stats.*.reltuples", "threshold"]


def test_format(anchor_evidence):
    fp = fingerprint(anchor_evidence, ANCHOR_INCLUDE)
    assert fp.startswith("sha256:") and len(fp) == 7 + 64


def test_stable_across_key_order(anchor_evidence):
    reordered = json.loads(json.dumps(anchor_evidence, sort_keys=True))
    reversed_keys = {k: anchor_evidence[k] for k in reversed(list(anchor_evidence))}
    assert fingerprint(anchor_evidence, ANCHOR_INCLUDE) == fingerprint(reordered, ANCHOR_INCLUDE)
    assert fingerprint(anchor_evidence, ANCHOR_INCLUDE) == fingerprint(reversed_keys, ANCHOR_INCLUDE)


def test_stable_across_set_order_and_hex_case(anchor_evidence):
    other = copy.deepcopy(anchor_evidence)
    other["runtime_report"]["effective_ca_spki"] = [s.upper() for s in
                                                    reversed(other["runtime_report"]["effective_ca_spki"])]
    assert fingerprint(anchor_evidence, ANCHOR_INCLUDE) == fingerprint(other, ANCHOR_INCLUDE)


def test_volatile_fields_ignored(anchor_evidence):
    other = copy.deepcopy(anchor_evidence)
    other["runtime_report"]["revision"] = "ledger-api-99999-new"
    other["root_feed"]["updated_at"] = "2030-01-01T00:00:00Z"
    assert fingerprint(anchor_evidence, ANCHOR_INCLUDE) == fingerprint(other, ANCHOR_INCLUDE)


def test_relevant_change_changes_fingerprint(anchor_evidence):
    other = copy.deepcopy(anchor_evidence)
    other["node_feed"]["line_security_floor"] = "v24.23.0"
    assert fingerprint(anchor_evidence, ANCHOR_INCLUDE) != fingerprint(other, ANCHOR_INCLUDE)


def test_row_bucket_values():
    assert row_bucket(0) == 0.0
    assert row_bucket(None) == 0.0
    assert row_bucket(600_000) == 5.75
    assert row_bucket(100_000) == 5.0


def mig_ev(rows):
    return {"pending_migrations": [{"version": "1", "file": "a.sql", "statements": []}],
            "pg_stats": {"ledger_entries": {"reltuples": rows, "relpages": rows // 7}},
            "threshold": 500000}


def test_stable_inside_row_bucket():
    # 600k and 610k share bucket 5.75; relpages is not selected
    assert fingerprint(mig_ev(600_000), MIG_INCLUDE) == fingerprint(mig_ev(610_000), MIG_INCLUDE)


def test_changes_across_row_buckets():
    assert fingerprint(mig_ev(100_000), MIG_INCLUDE) != fingerprint(mig_ev(750_000), MIG_INCLUDE)


def test_wildcard_select_and_missing_paths():
    sel = select({"pg_stats": {"b": {"reltuples": 5}, "a": {"reltuples": 7}}}, ["pg_stats.*.reltuples", "x.y"])
    assert sel == {"pg_stats.*.reltuples": {"a": {"reltuples": 7}, "b": {"reltuples": 5}}, "x.y": None}
    assert select({"a": 1}, ["a.b"]) == {"a.b": None}


def test_normalise_scalars():
    assert normalise({"reltuples": 1000.0}) == {"reltuples_bucket": 3.0}
    assert normalise(3.0) == 3
    assert normalise(["b", "a", "a"]) == ["a", "b"]
    assert normalise([{"z": 1}, {"a": 2}]) == [{"z": 1}, {"a": 2}]  # ordered records keep order
