#!/usr/bin/env python3
"""Shift the demo world (SRS §17.2). Writes ONLY to the GCS feeds bucket and the Cloud SQL
database. It never touches the repository, so no commit is involved in a claim going stale.

  python demo/world_shift.py reset        baseline feeds; ledger reset to 2 demo users + 100k rows;
                                          the demo index migration made pending again
  python demo/world_shift.py scenario-a   Node v24.22.1 security release + Demo Root G3 distrusted
  python demo/world_shift.py scenario-b   insert STALE_ROW_THRESHOLD x 1.5 rows, then ANALYZE

Database access: --database-url, or (default) a local Cloud SQL Auth Proxy started with your gcloud
login, using the migrator password read from Secret Manager (never printed).
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[1]
FEEDS = REPO / "demo" / "feeds"
DEMO_MIGRATION = "20261021090000"
DEMO_INDEX = "ledger_entries_merchant_idx"
BACKGROUND_ROWS = 100_000
BULK_SQL = ("INSERT INTO ledger_entries(user_id, merchant_id, amount_cents) "
            "SELECT 'u-' || (g %% 5000), 'm-' || lpad((g %% 1000)::text, 4, '0'), (g %% 997) + 1 "
            "FROM generate_series(1, %s) AS g")


def sh(*args: str, capture: bool = False) -> str:
    r = subprocess.run(args, check=True, text=True, capture_output=capture)
    return r.stdout.strip() if capture else ""


def gcloud_project() -> str:
    return os.environ.get("GCP_PROJECT_ID") or sh("gcloud", "config", "get-value", "project", capture=True)


def upload(bucket: str, files: list[Path]) -> None:
    for f in files:
        sh("gcloud", "storage", "cp", str(f), f"gs://{bucket}/feeds/{f.name}",
           "--cache-control=no-store", "--content-type=application/json")
        print(f"  uploaded feeds/{f.name}")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def database(args):
    if args.database_url:
        yield args.database_url
        return
    project = args.project
    conn_name = sh("gcloud", "sql", "instances", "describe", args.instance, "--project", project,
                   "--format=value(connectionName)", capture=True)
    password = sh("gcloud", "secrets", "versions", "access", "latest", "--secret", "stale-migrator-password",
                  "--project", project, capture=True)
    proxy = shutil.which("cloud-sql-proxy") or "/tmp/cloud-sql-proxy"
    port = _free_port()
    p = subprocess.Popen([proxy, "--gcloud-auth", "--port", str(port), conn_name],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.5)
        else:
            raise SystemExit("cloud-sql-proxy did not start")
        yield f"postgresql://stale_migrator:{quote(password, safe='')}@127.0.0.1:{port}/ledger"
    finally:
        p.terminate()


def reltuples(conn) -> int:
    row = conn.execute("SELECT reltuples::bigint FROM pg_class WHERE relname='ledger_entries'").fetchone()
    return int(row[0]) if row else -1


def cmd_reset(args) -> None:
    import psycopg

    print("feeds -> baseline")
    upload(args.bucket, sorted((FEEDS / "baseline").glob("*.json")))
    with database(args) as url, psycopg.connect(url, autocommit=True) as conn:
        if conn.execute("SELECT to_regclass('public.ledger_entries')").fetchone()[0] is None:
            raise SystemExit("ledger_entries does not exist yet: run a release (migrate) first")
        print("ledger -> 2 demo users + background rows")
        conn.execute("TRUNCATE ledger_entries RESTART IDENTITY")
        conn.execute("INSERT INTO ledger_entries(user_id, merchant_id, amount_cents) VALUES "
                     "('alpha','m-0001',6000),('alpha','m-0002',4000),('beta','m-0001',20000),('beta','m-0003',5000)")
        conn.execute(BULK_SQL, (BACKGROUND_ROWS,))
        conn.execute("ANALYZE ledger_entries")
        print(f"migration {DEMO_MIGRATION} -> pending again (approved when the table was small)")
        conn.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {DEMO_INDEX}")
        conn.execute("DELETE FROM schema_migrations WHERE version = %s", (DEMO_MIGRATION,))
        applied = [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")]
        print(f"  applied migrations: {applied}; reltuples(ledger_entries) = {reltuples(conn):,}")
    print("Cloud Run: reset to the baseline revision by running a manual release of the baseline commit.")


def cmd_scenario_a(args) -> None:
    print("scenario A: Node v24.22.1 security release published; root program distrusts Demo Root G3")
    upload(args.bucket, [FEEDS / "scenario-a" / "node-releases.json", FEEDS / "scenario-a" / "root-distrust.json"])


def cmd_scenario_b(args) -> None:
    import psycopg

    rows = int(args.threshold * 1.5)
    print(f"scenario B: ledger_entries grows by {rows:,} rows (threshold {args.threshold:,})")
    with database(args) as url, psycopg.connect(url, autocommit=True) as conn:
        t0 = time.time()
        conn.execute(BULK_SQL, (rows,))
        conn.execute("ANALYZE ledger_entries")  # reltuples only moves after ANALYZE
        print(f"  inserted in {time.time() - t0:.1f}s; reltuples(ledger_entries) = {reltuples(conn):,}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["reset", "scenario-a", "scenario-b"])
    ap.add_argument("--project", default=None)
    ap.add_argument("--bucket", default=None, help="default: <project>-stale-world")
    ap.add_argument("--instance", default="stale-pg")
    ap.add_argument("--database-url", default=None)
    ap.add_argument("--threshold", type=int, default=int(os.environ.get("STALE_ROW_THRESHOLD", "500000")))
    args = ap.parse_args()
    args.project = args.project or gcloud_project()
    args.bucket = args.bucket or f"{args.project}-stale-world"
    {"reset": cmd_reset, "scenario-a": cmd_scenario_a, "scenario-b": cmd_scenario_b}[args.command](args)
    print("done (no repository changes).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
