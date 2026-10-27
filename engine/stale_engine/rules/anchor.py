"""anchor-trusted rules R0–R4 (SRS §9.1)."""

from __future__ import annotations

from datetime import date
from typing import Any

from .. import semver, weakkeys
from ..models import Finding

NODE_IMAGE_VARIANT = "bookworm-slim"


def _r0(rr: dict[str, Any] | None) -> Finding | None:
    if not rr:
        return Finding(rule="R0", severity="high", detail={"reason": "runtime report unavailable"})
    if rr.get("unobservable_revisions"):
        return Finding(rule="R0", severity="high", detail={
            "reason": "revisions with traffic that cannot be observed",
            "revisions": sorted(rr["unobservable_revisions"])})
    if not rr.get("effective_ca_spki"):
        return Finding(rule="R0", severity="high", detail={
            "reason": "effective default CA set is empty (e.g. --use-openssl-ca)"})
    return None


def _r1(rr: dict[str, Any], nf: dict[str, Any]) -> Finding | None:
    floor, ver = nf.get("line_security_floor"), rr.get("node_version")
    if not floor or not ver or not semver.lt(ver, floor):
        return None
    return Finding(rule="R1", severity="high", detail={
        "node_version": ver, "line_security_floor": floor,
        "node_image_tag": f"{floor.lstrip('v')}-{NODE_IMAGE_VARIANT}"})


def _r2(nf: dict[str, Any], today: date) -> Finding | None:
    eol = nf.get("line_eol")
    if not eol or today <= date.fromisoformat(eol[:10]):
        return None
    return Finding(rule="R2", severity="high", detail={
        "line": nf.get("line"), "line_eol": eol[:10], "today": today.isoformat(), "soak_multiplier": 3})


def _r3(rr: dict[str, Any], rf: dict[str, Any]) -> Finding | None:
    effective = {s.lower() for s in rr.get("effective_ca_spki", [])}
    distrusted = {s.lower() for s in rf.get("distrusted_spki", [])}
    hit = sorted(effective & distrusted)
    if not hit:
        return None
    subjects = rr.get("effective_ca_subjects", {})
    return Finding(rule="R3", severity="high", detail={
        "spki": hit, "subjects": {s: subjects.get(s, "") for s in hit}})


def _r4(rr: dict[str, Any]) -> Finding | None:
    effective = {s.lower() for s in rr.get("effective_ca_spki", [])}
    subjects = rr.get("effective_ca_subjects", {})
    weak = []
    for spki, key in sorted(rr.get("effective_ca_keys", {}).items()):
        if spki.lower() not in effective:
            continue
        bad, why = weakkeys.is_weak_key(key)
        if bad:
            weak.append({"spki": spki.lower(), "subject": subjects.get(spki, ""), "reason": why})
    if not weak:
        return None
    return Finding(rule="R4", severity="critical", detail={"anchors": weak})


def evaluate(evidence: dict[str, Any], rules: set[str], today: date) -> list[Finding]:
    rr = evidence.get("runtime_report")
    r0 = _r0(rr)
    if r0:
        return [r0]  # nothing else can be evaluated without an observable trust store
    nf = evidence.get("node_feed") or {}
    rf = evidence.get("root_feed") or {}
    checks = {"R1": lambda: _r1(rr, nf), "R2": lambda: _r2(nf, today),
              "R3": lambda: _r3(rr, rf), "R4": lambda: _r4(rr)}
    return [f for rule in ("R1", "R2", "R3", "R4") if rule in rules and (f := checks[rule]())]


def facts(findings: list[Finding]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    spki: set[str] = set()
    for f in findings:
        if f.rule == "R1":
            out["node_floor"] = f.detail["line_security_floor"]
            out["node_image_tag"] = f.detail["node_image_tag"]
        elif f.rule == "R2":
            out["node_line_eol"] = f.detail["line_eol"]
        elif f.rule == "R3":
            spki.update(f.detail["spki"])
        elif f.rule == "R4":
            spki.update(a["spki"] for a in f.detail["anchors"])
    if spki:
        out["add_excluded_spki"] = sorted(spki)
    return out
