from datetime import date

import pytest

from stale_engine import issue_format, rules
from stale_engine.models import LockEntry

from .conftest import BASELINE_PENDING, G3_SPKI, migration_evidence

TODAY = date(2026, 10, 25)


@pytest.fixture
def scenario_a(g3_evidence, anchor_claim):
    g3_evidence["runtime_report"]["node_version"] = "v24.22.0"
    g3_evidence["root_feed"]["distrusted_spki"] = [G3_SPKI]
    findings = rules.evaluate(anchor_claim, g3_evidence, TODAY)
    return anchor_claim, findings, g3_evidence, rules.facts(anchor_claim, findings)


def test_round_trip_anchor(scenario_a):
    claim, findings, ev, facts = scenario_a
    title, body = issue_format.render(claim, findings, ev, facts)
    parsed = issue_format.parse(body)
    assert parsed == {"claim": "anchor-trusted", "rules": ["R1", "R3", "R4"],
                      "playbook": ".stale/playbooks/anchor-trusted.md",
                      "allowPaths": ["app/ledger-api/Dockerfile", "app/ledger-api/deploy/trust/**"],
                      "facts": facts}
    assert title == "[stale] anchor-trusted: R1 – Node v24.22.0 is below the v24.22.1 security floor"


def test_machine_block_is_byte_exact(scenario_a):
    claim, findings, ev, facts = scenario_a
    _, body1 = issue_format.render(claim, findings, ev, facts)
    _, body2 = issue_format.render(claim, list(findings), dict(reversed(list(ev.items()))),
                                   dict(reversed(list(facts.items()))))
    block = lambda b: b[b.index(issue_format.BEGIN):b.index(issue_format.END) + len(issue_format.END)]
    assert block(body1) == block(body2)
    assert block(body1).startswith("<!-- stale:machine:begin -->\n```json\n{")
    assert block(body1).endswith("}\n```\n<!-- stale:machine:end -->")


def test_body_contents(scenario_a):
    claim, findings, ev, facts = scenario_a
    _, body = issue_format.render(claim, findings, ev, facts,
                                  LockEntry(minted_at="2026-10-24T10:00:00Z", revision="ledger-api-00011"))
    assert body.startswith("## Stale claim: anchor-trusted\n**Assertion:** Every revision")
    assert "**Findings:** R1 (high), R3 (high), R4 (critical)" in body
    assert "CN=Demo Root G3 (sha256:3eab3fba…)" in body
    assert "minted 2026-10-24T10:00:00Z on ledger-api-00011" in body
    assert "security floor for v24.x: v24.22.1" in body


def test_round_trip_migration(tmp_path, migration_claim):
    ev = migration_evidence(tmp_path, BASELINE_PENDING, rows=850_000)
    findings = rules.evaluate(migration_claim, ev)
    facts = rules.facts(migration_claim, findings)
    title, body = issue_format.render(migration_claim, findings, ev, facts)
    assert title == "[stale] migration-safe: M1 – blocking CREATE INDEX on large table ledger_entries"
    assert issue_format.parse(body)["facts"] == facts
    assert "| Rows in `ledger_entries` (estimate) | never minted | 850,000 (threshold 500,000) |" in body


def test_parse_tolerates_crlf_and_surrounding_text(scenario_a):
    claim, findings, ev, facts = scenario_a
    _, body = issue_format.render(claim, findings, ev, facts)
    edited = ("Note from a human\r\n\r\n" + body + "\r\n\r\nmore text").replace("\n", "\r\n")
    assert issue_format.parse(edited)["claim"] == "anchor-trusted"


def test_parse_without_block_raises():
    with pytest.raises(ValueError):
        issue_format.parse("## Stale claim: x\nno block here")
