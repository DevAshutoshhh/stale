"""Evidence collectors. Each returns a JSON-serialisable dict (SRS §6.2, §9)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import httpget, semver
from ..migrate import runner


def runtime_report(*, url: str | None = None, file: str | None = None) -> dict[str, Any]:
    """Serving: GET <tag-url>/internal/runtime. Candidate: the JSON printed inside the image (§7)."""
    if file:
        return httpget.get_json(file)
    if not url:
        raise RuntimeError("runtime_report needs --runtime-url or --runtime-file")
    return httpget.get_json(url.rstrip("/") + "/internal/runtime")


def node_feed(config: dict[str, Any], node_version: str | None) -> dict[str, Any]:
    """Derive the security floor and EOL for the running Node major line (SRS §9.1)."""
    releases = httpget.get_json(config["releases"])
    schedule = httpget.get_json(config["schedule"])
    if not node_version:
        raise RuntimeError("node_feed needs runtime_report.node_version")
    line = f"v{semver.major(node_version)}"
    in_line = [r for r in releases if semver.major(r["version"]) == semver.major(node_version)]
    security = [r["version"] for r in in_line if r.get("security")]
    floor = max(security, key=semver.parse) if security else None
    latest = max((r["version"] for r in in_line), key=semver.parse) if in_line else None
    return {"line": line, "line_security_floor": floor, "line_latest": latest,
            "line_eol": (schedule.get(line) or {}).get("end")}


def root_feed(config: dict[str, Any]) -> dict[str, Any]:
    feed = httpget.get_json(config["url"])
    if feed.get("schema") != "stale.rootfeed/v1":
        raise RuntimeError(f"root feed schema {feed.get('schema')!r} != stale.rootfeed/v1")
    entries = feed.get("distrusted", [])
    return {"updated_at": feed.get("updated_at"),
            "distrusted_spki": sorted({e["spki_sha256"].lower() for e in entries}),
            "distrusted": entries}


PG_STATS_SQL = ("SELECT relname, reltuples::bigint, relpages FROM pg_class "
                "WHERE relkind='r' AND relnamespace='public'::regnamespace")


def pg_stats(conn) -> dict[str, Any]:
    return {name: {"reltuples": int(rt), "relpages": int(rp)} for name, rt, rp in conn.execute(PG_STATS_SQL)}


def pending_migrations(conn, directory: Path, repo_root: Path | None = None) -> list[dict[str, Any]]:
    files = runner.load_dir(directory)
    todo = runner.pending(files, runner.applied_versions(conn) if conn is not None else set())
    return [f.describe(rel_to=repo_root) for f in todo]
