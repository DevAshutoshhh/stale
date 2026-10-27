"""Deterministic rules. Only these functions decide whether a claim is stale (SRS §2.1)."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..models import Claim, Finding
from . import anchor, migration

RULE_FAMILIES = {"R": anchor, "M": migration}


def evaluate(claim: Claim, evidence: dict[str, Any], today: date | None = None) -> list[Finding]:
    """Run every rule listed in the claim against `evidence`. R0 always runs for anchor claims."""
    today = today or date.today()
    findings: list[Finding] = []
    if any(r.startswith("R") for r in claim.spec.rules):
        findings += anchor.evaluate(evidence, set(claim.spec.rules) | {"R0"}, today)
    if any(r.startswith("M") for r in claim.spec.rules):
        findings += migration.evaluate(evidence, set(claim.spec.rules))
    return findings


def facts(claim: Claim, findings: list[Finding]) -> dict[str, Any]:
    """Everything the remediation agent needs, so it never looks anything up (SRS §6.5)."""
    if claim.id == "anchor-trusted" or any(f.rule.startswith("R") for f in findings):
        return anchor.facts(findings)
    return migration.facts(findings)
