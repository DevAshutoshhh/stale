"""Shadow migration re-proof (SRS §12.1), run against a throwaway postgres:18 database.

1. Replay the already-applied migrations. 2. Seed ledger_entries to threshold × 1.2 rows, ANALYZE.
3. A writer inserts a row every 10 ms and records latency. 4. Apply the pending migrations.
5. Meanwhile poll pg_locks every 50 ms. 6. Fail if ShareLock/AccessExclusiveLock is held > 100 ms,
writer p99 > 250 ms, or any index is INVALID. 7. Write shadow-report.json.

The target database name MUST start with "shadow": this module drops and recreates its schema.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict

from ..migrate import runner

BLOCKING_MODES = ("ShareLock", "AccessExclusiveLock")
MAX_BLOCKING_MS = 100
MAX_WRITER_P99_MS = 250
WRITE_EVERY_S = 0.010
POLL_EVERY_S = 0.050
LOCKS_SQL = ("SELECT mode FROM pg_locks l JOIN pg_class c ON c.oid=l.relation "
             "WHERE c.relname='ledger_entries' AND l.pid <> pg_backend_pid() AND l.granted")
SEED_SQL = ("INSERT INTO ledger_entries(user_id, merchant_id, amount_cents) "
            "SELECT 'u-' || (g %% 5000), 'm-' || lpad((g %% 1000)::text, 4, '0'), (g %% 997) + 1 "
            "FROM generate_series(1, %s) AS g")


class ShadowGuardError(RuntimeError):
    pass


def _check_shadow(url: str) -> None:
    db = conninfo_to_dict(url).get("dbname") or ""
    if not db.startswith("shadow"):
        raise ShadowGuardError(f"refusing to run: database {db!r} does not start with 'shadow'")


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


class _Writer(threading.Thread):
    def __init__(self, url: str, stop: threading.Event):
        super().__init__(daemon=True)
        self.url, self.stop, self.latencies_ms, self.errors = url, stop, [], 0

    def run(self) -> None:
        with psycopg.connect(self.url, autocommit=True) as conn:
            while not self.stop.is_set():
                t0 = time.perf_counter()
                try:
                    conn.execute("INSERT INTO ledger_entries(user_id, merchant_id, amount_cents) "
                                 "VALUES ('shadow-writer', 'm-0000', 1)")
                except psycopg.Error:
                    self.errors += 1
                self.latencies_ms.append((time.perf_counter() - t0) * 1000)
                time.sleep(max(0.0, WRITE_EVERY_S - (time.perf_counter() - t0)))


class _Poller(threading.Thread):
    def __init__(self, url: str, stop: threading.Event, t0: float):
        super().__init__(daemon=True)
        self.url, self.stop, self.t0 = url, stop, t0
        self.timeline: list[dict[str, Any]] = []

    def run(self) -> None:
        with psycopg.connect(self.url, autocommit=True) as conn:
            while not self.stop.is_set():
                modes = sorted({r[0] for r in conn.execute(LOCKS_SQL)})
                self.timeline.append({"t_ms": round((time.perf_counter() - self.t0) * 1000), "modes": modes})
                time.sleep(POLL_EVERY_S)


def blocking_spans(timeline: list[dict[str, Any]]) -> dict[str, int]:
    """Longest contiguous observed span (ms) per blocking mode."""
    spans: dict[str, int] = {}
    for mode in BLOCKING_MODES:
        start = last = None
        for e in timeline:
            if mode in e["modes"]:
                start = e["t_ms"] if start is None else start
                last = e["t_ms"]
                spans[mode] = max(spans.get(mode, 0), last - start + round(POLL_EVERY_S * 1000))
            else:
                start = None
    return spans


def run(url: str, migrations_dir: Path, *, threshold: int, applied_versions: set[str] | None = None,
        out_dir: Path | None = None, rows: int | None = None, log=print) -> dict[str, Any]:
    _check_shadow(url)
    files = runner.load_dir(migrations_dir)
    all_versions = {f.version for f in files}
    applied = all_versions if applied_versions is None else set(applied_versions) & all_versions
    pending = sorted(all_versions - applied)
    rows = rows or int(threshold * 1.2)

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")
    runner.apply(url, migrations_dir, only=applied, log=lambda m: None)
    t_seed = time.perf_counter()
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(SEED_SQL, (rows,))
        conn.execute("ANALYZE ledger_entries")
        reltuples = conn.execute("SELECT reltuples::bigint FROM pg_class WHERE relname='ledger_entries'").fetchone()[0]
    log(f"shadow: replayed {sorted(applied)}; seeded {rows:,} rows in {time.perf_counter() - t_seed:.1f}s "
        f"(reltuples={reltuples:,}); pending {pending or 'none'}")

    stop = threading.Event()
    t0 = time.perf_counter()
    writer, poller = _Writer(url, stop), _Poller(url, stop, t0)
    writer.start()
    poller.start()
    time.sleep(0.3)
    migrate_error = None
    t_mig = time.perf_counter()
    try:
        runner.apply(url, migrations_dir, only=set(pending), log=lambda m: log(f"  {m}"))
    except runner.MigrationError as e:
        migrate_error = str(e)
    migrate_ms = round((time.perf_counter() - t_mig) * 1000)
    time.sleep(0.3)
    stop.set()
    writer.join(5)
    poller.join(5)

    with psycopg.connect(url, autocommit=True) as conn:
        invalid = runner.invalid_indexes(conn)

    spans = blocking_spans(poller.timeline)
    lat = writer.latencies_ms
    writer_stats = {"inserts": len(lat), "errors": writer.errors, "p50_ms": round(_pct(lat, 50), 1),
                    "p99_ms": round(_pct(lat, 99), 1), "max_ms": round(max(lat, default=0), 1)}
    failures = []
    for mode, ms in spans.items():
        if ms > MAX_BLOCKING_MS:
            failures.append(f"{mode} held on ledger_entries for ~{ms} ms (> {MAX_BLOCKING_MS} ms)")
    if writer_stats["p99_ms"] > MAX_WRITER_P99_MS:
        failures.append(f"writer p99 {writer_stats['p99_ms']} ms (> {MAX_WRITER_P99_MS} ms)")
    if invalid:
        failures.append(f"INVALID index(es): {', '.join(invalid)}")
    if migrate_error:
        failures.append(f"migration failed: {migrate_error}")

    rep = {"passed": not failures, "failures": failures, "threshold": threshold, "rows_seeded": rows,
           "reltuples": reltuples, "applied_before": sorted(applied), "pending": pending,
           "migrate_ms": migrate_ms, "blocking_spans_ms": spans, "writer": writer_stats,
           "invalid_indexes": invalid, "migrate_error": migrate_error,
           "lock_timeline": [e for e in poller.timeline if e["modes"]]}
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "shadow-report.json").write_text(json.dumps(rep, indent=2) + "\n")
    return rep


def main() -> int:  # pragma: no cover - thin wrapper for local runs
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database-url", required=True)
    ap.add_argument("--dir", type=Path, default=Path("app/ledger-api/db/migrations"))
    ap.add_argument("--threshold", type=int, default=500_000)
    ap.add_argument("--applied", default=None, help="comma-separated versions treated as already applied")
    ap.add_argument("--out", type=Path, default=Path(".stale/out"))
    a = ap.parse_args()
    applied = set(a.applied.split(",")) if a.applied else None
    rep = run(a.database_url, a.dir, threshold=a.threshold, applied_versions=applied, out_dir=a.out)
    summary = {k: rep[k] for k in ("passed", "failures", "rows_seeded", "pending", "migrate_ms",
                                   "blocking_spans_ms", "writer", "invalid_indexes")}
    print(json.dumps(summary, indent=2))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
