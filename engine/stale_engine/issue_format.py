"""Claim issue title/body rendering and machine-block parsing (SRS §6.5).

The machine block is byte-exact: RFC 8785 JSON between fixed markers, so the Duo flow and the
engine read the same bytes.
"""

from __future__ import annotations

import json
import re
from typing import Any

import rfc8785

from .models import Claim, Finding, LockEntry

BEGIN = "<!-- stale:machine:begin -->"
END = "<!-- stale:machine:end -->"
_BLOCK_RE = re.compile(re.escape(BEGIN) + r"\n```json\n(.*?)\n```\n" + re.escape(END), re.S)


def machine_block(data: dict[str, Any]) -> str:
    return f"{BEGIN}\n```json\n{rfc8785.dumps(data).decode('utf-8')}\n```\n{END}"


def parse(body: str) -> dict[str, Any]:
    m = _BLOCK_RE.search(body.replace("\r\n", "\n"))
    if not m:
        raise ValueError("no stale machine block in issue body")
    return json.loads(m.group(1))


def _summary(f: Finding) -> str:
    d = f.detail
    return {
        "R0": lambda: f"trust store unobservable ({d.get('reason', '')})",
        "R1": lambda: f"Node {d.get('node_version')} is below the {d.get('line_security_floor')} security floor",
        "R2": lambda: f"Node line {d.get('line')} reached end of life on {d.get('line_eol')}",
        "R3": lambda: "a distrusted root is in the effective CA set",
        "R4": lambda: "a weak-key root is in the effective CA set",
        "M1": lambda: f"blocking CREATE INDEX on large table {d.get('table')}",
        "M3": lambda: f"{d.get('lock_mode')} lock on large table {d.get('table')} without lock_timeout",
        "M4": lambda: "CONCURRENTLY inside a transactional migration",
        "M5": lambda: "concurrent index build is not retry-safe",
    }.get(f.rule, lambda: f.rule)()


def title(claim: Claim, findings: list[Finding]) -> str:
    first = findings[0]
    return f"[stale] {claim.id}: {first.rule} – {_summary(first)}"


def _minted(lock: LockEntry | None) -> str:
    if not lock or not lock.minted_at:
        return "never minted"
    return f"minted {lock.minted_at} on {lock.revision or '?'}"


def _anchor_rows(evidence: dict[str, Any], findings: list[Finding], minted: str) -> list[str]:
    rr = evidence.get("runtime_report") or {}
    nf = evidence.get("node_feed") or {}
    subjects = rr.get("effective_ca_subjects", {})
    by = {f.rule: f for f in findings}
    floor = nf.get("line_security_floor") or "unknown"
    rows = [f"| Node | {minted} | {rr.get('node_version', '?')} (security floor for "
            f"{nf.get('line', '?')}.x: {floor}) |"]
    dist = by.get("R3").detail["spki"] if "R3" in by else []
    rows.append("| Distrusted anchors in effective set | none | "
                + (", ".join(f"{subjects.get(s, '?')} (sha256:{s[:8]}…)" for s in dist) or "none") + " |")
    weak = by.get("R4").detail["anchors"] if "R4" in by else []
    rows.append("| Weak-key anchors in effective set | none | "
                + (", ".join(f"{a['subject'] or '?'} (sha256:{a['spki'][:8]}…)" for a in weak) or "none") + " |")
    if "R2" in by:
        rows.append(f"| Node line EOL | — | {by['R2'].detail['line_eol']} (passed) |")
    return rows


def _migration_rows(evidence: dict[str, Any], findings: list[Finding], minted: str) -> list[str]:
    stats = evidence.get("pg_stats") or {}
    threshold = evidence.get("threshold")
    rows = []
    for table in sorted({f.detail.get("table") for f in findings if f.detail.get("table")}):
        rows.append(f"| Rows in `{table}` (estimate) | {minted} | "
                    f"{int(stats.get(table, {}).get('reltuples', 0)):,} (threshold {threshold:,}) |")
    for f in sorted({f.detail["file"] for f in findings}):
        rules = sorted({x.rule for x in findings if x.detail["file"] == f})
        rows.append(f"| Pending migration `{f}` | — | {', '.join(rules)} |")
    return rows


def render(claim: Claim, findings: list[Finding], evidence: dict[str, Any],
           facts: dict[str, Any], lock: LockEntry | None = None) -> tuple[str, str]:
    minted = _minted(lock)
    rows = (_anchor_rows if any(f.rule.startswith("R") for f in findings) else _migration_rows)(
        evidence, findings, minted)
    machine = {"claim": claim.id, "rules": sorted({f.rule for f in findings}),
               "playbook": claim.spec.remediation.playbook,
               "allowPaths": claim.spec.remediation.allowPaths, "facts": facts}
    body = "\n".join([
        f"## Stale claim: {claim.id}",
        f"**Assertion:** {claim.spec.assertion.strip()}",
        "**Findings:** " + ", ".join(f"{f.rule} ({f.severity})" for f in findings),
        "",
        "| Evidence | Last minted | Now |",
        "|---|---|---|",
        *rows,
        "",
        machine_block(machine),
        "",
        "_Opened by the Stale control loop. Only `stale mint` closes this issue, after a verified release._",
    ])
    return title(claim, findings), body
