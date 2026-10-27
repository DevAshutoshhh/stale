from datetime import date

from stale_engine import rules
from stale_engine.rules import anchor

from .conftest import G3_SPKI

TODAY = date(2026, 10, 25)
ALL = {"R1", "R2", "R3", "R4"}


def rule_ids(findings):
    return [f.rule for f in findings]


def test_baseline_passes(anchor_evidence):
    assert anchor.evaluate(anchor_evidence, ALL, TODAY) == []


# R0 -------------------------------------------------------------------------------------------
def test_r0_missing_report(anchor_evidence):
    del anchor_evidence["runtime_report"]
    assert rule_ids(anchor.evaluate(anchor_evidence, ALL, TODAY)) == ["R0"]


def test_r0_empty_ca_set(anchor_evidence):
    anchor_evidence["runtime_report"]["effective_ca_spki"] = []
    f = anchor.evaluate(anchor_evidence, ALL, TODAY)
    assert rule_ids(f) == ["R0"] and "empty" in f[0].detail["reason"]


def test_r0_unobservable_revision(anchor_evidence):
    anchor_evidence["runtime_report"]["unobservable_revisions"] = ["ledger-api-0009-abc"]
    assert rule_ids(anchor.evaluate(anchor_evidence, ALL, TODAY)) == ["R0"]


# R1 -------------------------------------------------------------------------------------------
def test_r1_fires_below_floor(anchor_evidence):
    anchor_evidence["runtime_report"]["node_version"] = "v24.22.0"
    f = anchor.evaluate(anchor_evidence, ALL, TODAY)
    assert rule_ids(f) == ["R1"]
    assert f[0].detail["node_image_tag"] == "24.22.1-bookworm-slim"


def test_r1_quiet_at_or_above_floor(anchor_evidence):
    anchor_evidence["runtime_report"]["node_version"] = "v24.23.0"
    assert anchor.evaluate(anchor_evidence, ALL, TODAY) == []


def test_r1_quiet_without_floor(anchor_evidence):
    anchor_evidence["runtime_report"]["node_version"] = "v24.1.0"
    anchor_evidence["node_feed"]["line_security_floor"] = None
    assert anchor.evaluate(anchor_evidence, ALL, TODAY) == []


# R2 -------------------------------------------------------------------------------------------
def test_r2_fires_after_eol(anchor_evidence):
    anchor_evidence["node_feed"]["line_eol"] = "2026-04-30"
    f = anchor.evaluate(anchor_evidence, ALL, TODAY)
    assert rule_ids(f) == ["R2"] and f[0].detail["soak_multiplier"] == 3


def test_r2_quiet_on_eol_day_and_before(anchor_evidence):
    anchor_evidence["node_feed"]["line_eol"] = TODAY.isoformat()
    assert anchor.evaluate(anchor_evidence, ALL, TODAY) == []


# R3 -------------------------------------------------------------------------------------------
def test_r3_fires_on_distrusted_anchor(g3_evidence):
    g3_evidence["root_feed"]["distrusted_spki"] = [G3_SPKI.upper()]
    f = anchor.evaluate(g3_evidence, {"R3"}, TODAY)
    assert rule_ids(f) == ["R3"]
    assert f[0].detail["spki"] == [G3_SPKI]
    assert f[0].detail["subjects"][G3_SPKI] == "CN=Demo Root G3"


def test_r3_quiet_when_distrusted_root_absent(anchor_evidence):
    anchor_evidence["root_feed"]["distrusted_spki"] = [G3_SPKI]
    assert anchor.evaluate(anchor_evidence, ALL, TODAY) == []


# R4 -------------------------------------------------------------------------------------------
def test_r4_detects_k4(g3_evidence):
    f = anchor.evaluate(g3_evidence, ALL, TODAY)
    assert rule_ids(f) == ["R4"]
    assert f[0].severity == "critical"
    assert f[0].detail["anchors"][0]["spki"] == G3_SPKI


def test_r4_quiet_for_random_key(anchor_evidence):
    assert anchor.evaluate(anchor_evidence, {"R4"}, TODAY) == []


def test_r4_detects_short_rsa(anchor_evidence):
    rr = anchor_evidence["runtime_report"]
    rr["effective_ca_keys"]["b" * 64]["bits"] = 1024
    assert rule_ids(anchor.evaluate(anchor_evidence, {"R4"}, TODAY)) == ["R4"]


def test_r4_ignores_keys_not_in_effective_set(g3_evidence):
    g3_evidence["runtime_report"]["effective_ca_spki"].remove(G3_SPKI)
    assert anchor.evaluate(g3_evidence, ALL, TODAY) == []


def test_rules_not_listed_are_skipped(g3_evidence):
    g3_evidence["runtime_report"]["node_version"] = "v24.0.0"
    assert rule_ids(anchor.evaluate(g3_evidence, {"R1"}, TODAY)) == ["R1"]


# scenario A end to end -------------------------------------------------------------------------
def test_scenario_a_facts(g3_evidence, anchor_claim):
    g3_evidence["runtime_report"]["node_version"] = "v24.22.0"
    g3_evidence["root_feed"]["distrusted_spki"] = [G3_SPKI]
    findings = rules.evaluate(anchor_claim, g3_evidence, TODAY)
    assert rule_ids(findings) == ["R1", "R3", "R4"]
    assert rules.facts(anchor_claim, findings) == {
        "node_floor": "v24.22.1", "node_image_tag": "24.22.1-bookworm-slim",
        "add_excluded_spki": [G3_SPKI]}


def test_after_remediation_g3_filtered(g3_evidence, anchor_claim):
    # The trust policy removed G3 from the effective set and the image is on the floor.
    g3_evidence["runtime_report"]["effective_ca_spki"].remove(G3_SPKI)
    g3_evidence["root_feed"]["distrusted_spki"] = [G3_SPKI]
    assert rules.evaluate(anchor_claim, g3_evidence, TODAY) == []


def test_r2_fact(anchor_evidence, anchor_claim):
    anchor_evidence["node_feed"]["line_eol"] = "2026-01-01"
    f = rules.evaluate(anchor_claim, anchor_evidence, TODAY)
    assert rules.facts(anchor_claim, f) == {"node_line_eol": "2026-01-01"}
