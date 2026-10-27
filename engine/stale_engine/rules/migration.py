"""migration-safe rules M1, M3, M4, M5 (SRS §9.2; M2 is out of scope per the build prompt).

A "large" table has reltuples >= threshold.
"""

from __future__ import annotations

from typing import Any

from .. import config
from ..migrate.lockmodes import BLOCKING
from ..models import Finding

MAX_LOCK_TIMEOUT_MS = 2000


def _large(table: str | None, stats: dict[str, Any], threshold: int) -> bool:
    if not table or table not in stats:
        return False
    return float(stats[table].get("reltuples") or 0) >= threshold


def evaluate(evidence: dict[str, Any], rules: set[str]) -> list[Finding]:
    pending = evidence.get("pending_migrations") or []
    stats = evidence.get("pg_stats") or {}
    threshold = int(evidence.get("threshold") or config.row_threshold())
    findings: list[Finding] = []

    for mig in pending:
        file, stmts = mig["file"], mig["statements"]
        lock_timeout_ms: int | None = None
        dropped_concurrently: set[str] = set()
        base = {"file": file, "version": mig["version"]}

        for i, s in enumerate(stmts):
            kind, table = s["kind"], s.get("table")
            if kind == "set_lock_timeout":
                lock_timeout_ms = s.get("sets_lock_timeout_ms")
                continue
            if kind == "reset_lock_timeout":
                lock_timeout_ms = None
                continue
            large = _large(table, stats, threshold)
            rows = stats.get(table, {}).get("reltuples") if table else None
            where = {**base, "statement": i, "sql": s["sql_normalized"], "table": table,
                     "reltuples": rows, "threshold": threshold}

            if "M1" in rules and kind == "create_index" and not s.get("concurrent") and large:
                findings.append(Finding(rule="M1", severity="high", detail={
                    **where, "index_name": s.get("index_name"), "lock_mode": s.get("lock_mode")}))

            if ("M3" in rules and large and s.get("lock_mode") in BLOCKING
                    and not (lock_timeout_ms and 0 < lock_timeout_ms <= MAX_LOCK_TIMEOUT_MS)):
                findings.append(Finding(rule="M3", severity="high", detail={
                    **where, "lock_mode": s.get("lock_mode"), "lock_timeout_ms": lock_timeout_ms}))

            if s.get("concurrent"):
                if "M4" in rules and not mig.get("no_transaction"):
                    findings.append(Finding(rule="M4", severity="high", detail={
                        **where, "reason": "CONCURRENTLY cannot run inside a transaction; "
                                           "add the '-- stale:no-transaction' header"}))
                if kind == "drop_index" and s.get("if_exists") and s.get("index_name"):
                    dropped_concurrently.add(s["index_name"])
                if "M5" in rules and kind == "create_index":
                    name = s.get("index_name")
                    if s.get("if_not_exists"):
                        reason = "CREATE INDEX CONCURRENTLY IF NOT EXISTS skips a leftover INVALID index"
                    elif not name or name not in dropped_concurrently:
                        reason = "no preceding DROP INDEX CONCURRENTLY IF EXISTS of the same index"
                    else:
                        reason = None
                    if reason:
                        findings.append(Finding(rule="M5", severity="high", detail={
                            **where, "index_name": name, "reason": reason}))
    return findings


def facts(findings: list[Finding]) -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    for f in findings:
        d = f.detail
        entry = files.setdefault(d["file"], {"file": d["file"], "version": d["version"], "rules": [],
                                             "table": d.get("table"), "index_name": d.get("index_name")})
        if f.rule not in entry["rules"]:
            entry["rules"].append(f.rule)
        entry["index_name"] = entry["index_name"] or d.get("index_name")
        if d.get("reltuples") is not None:
            entry["reltuples"] = int(d["reltuples"])
        entry["threshold"] = d.get("threshold")
    for e in files.values():
        e["rules"].sort()
    return {"files": sorted(files.values(), key=lambda e: e["version"]),
            "lock_timeout": "1500ms", "no_transaction_header": "-- stale:no-transaction"}
