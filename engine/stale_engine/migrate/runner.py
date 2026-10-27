"""Plain-SQL migration runner (SRS §10).

Files: <14-digit-version>_<name>.sql with `-- migrate:up` / `-- migrate:down` sections and an
optional first line `-- stale:no-transaction` (autocommit, one statement at a time).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pglast

from .lockmodes import classify

NO_TX_HEADER = "-- stale:no-transaction"
FILE_RE = re.compile(r"^(\d{14})_([a-z0-9_]+)\.sql$")
ADVISORY_KEY_SQL = "hashtext('stale-migrate')"
TRACKING_DDL = """CREATE TABLE IF NOT EXISTS schema_migrations (
  version text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now(),
  checksum text NOT NULL
)"""
INVALID_INDEXES_SQL = "SELECT indexrelid::regclass::text FROM pg_index WHERE NOT indisvalid ORDER BY 1"


class MigrationError(RuntimeError):
    """A migration failed or left an INVALID index. The CLI maps this to exit 1."""


@dataclass(frozen=True)
class MigrationFile:
    version: str
    name: str
    path: Path
    no_transaction: bool
    up_sql: str
    down_sql: str
    checksum: str

    def statements(self) -> list[str]:
        return list(pglast.split(self.up_sql, with_parser=True)) if self.up_sql.strip() else []

    def describe(self, rel_to: Path | None = None) -> dict[str, Any]:
        """Evidence shape for `pending_migrations` (SRS §9.2)."""
        path = self.path
        if rel_to is not None:
            try:
                path = self.path.resolve().relative_to(rel_to.resolve())
            except ValueError:
                pass
        return {"version": self.version, "file": path.as_posix(), "no_transaction": self.no_transaction,
                "statements": [classify(s).to_dict() for s in self.statements()]}


def parse_file(path: Path) -> MigrationFile:
    m = FILE_RE.match(path.name)
    if not m:
        raise MigrationError(f"{path.name}: filename must be <14-digit-version>_<name>.sql")
    raw = path.read_bytes()
    text = raw.decode("utf-8").replace("\r\n", "\n")
    lines = text.split("\n")
    no_tx = lines[0].strip() == NO_TX_HEADER
    up: list[str] | None = None
    down: list[str] | None = None
    current: list[str] | None = None
    for line in lines:
        tag = line.strip()
        if tag == "-- migrate:up":
            up = current = []
        elif tag == "-- migrate:down":
            down = current = []
        elif current is not None:
            current.append(line)
    if up is None:
        raise MigrationError(f"{path.name}: missing '-- migrate:up' section")
    return MigrationFile(version=m.group(1), name=m.group(2), path=path, no_transaction=no_tx,
                         up_sql="\n".join(up).strip(), down_sql="\n".join(down or []).strip(),
                         checksum="sha256:" + hashlib.sha256(raw).hexdigest())


def load_dir(directory: Path) -> list[MigrationFile]:
    files = [parse_file(p) for p in sorted(directory.glob("*.sql"))]
    versions = [f.version for f in files]
    if len(set(versions)) != len(versions):
        raise MigrationError("duplicate migration versions")
    return files


def applied_versions(conn) -> set[str]:
    exists = conn.execute("SELECT to_regclass('public.schema_migrations') IS NOT NULL").fetchone()[0]
    if not exists:
        return set()
    return {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}


def pending(files: list[MigrationFile], applied: set[str]) -> list[MigrationFile]:
    return [f for f in files if f.version not in applied]


def invalid_indexes(conn) -> list[str]:
    return [r[0] for r in conn.execute(INVALID_INDEXES_SQL)]


def apply(database_url: str, directory: Path, log: Callable[[str], None] = print,
          only: set[str] | None = None) -> list[str]:
    """Apply pending migrations in order. Returns the versions applied.

    `only` restricts the run to those versions (the shadow migration replays "applied" files first).
    Raises MigrationError on any failure or when an INVALID index exists after a file.
    """
    import psycopg

    files = [f for f in load_dir(directory) if only is None or f.version in only]
    applied_now: list[str] = []
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(TRACKING_DDL)
        conn.execute(f"SELECT pg_advisory_lock({ADVISORY_KEY_SQL})")
        try:
            todo = pending(files, applied_versions(conn))
            if not todo:
                log("no pending migrations")
            for mf in todo:
                mode = "no-transaction" if mf.no_transaction else "transaction"
                log(f"applying {mf.version}_{mf.name} ({mode})")
                stmts = mf.statements()
                try:
                    if mf.no_transaction:
                        try:
                            for sql in stmts:
                                log(f"  > {sql}")
                                conn.execute(sql)
                        finally:
                            conn.execute("RESET lock_timeout")
                        bad = invalid_indexes(conn)
                        if bad:
                            raise MigrationError(f"{mf.version}: INVALID index(es) after file: {', '.join(bad)}")
                        conn.execute("INSERT INTO schema_migrations(version, checksum) VALUES (%s, %s)",
                                     (mf.version, mf.checksum))
                    else:
                        with conn.transaction():
                            for sql in stmts:
                                log(f"  > {sql}")
                                conn.execute(sql)
                            bad = invalid_indexes(conn)
                            if bad:
                                raise MigrationError(
                                    f"{mf.version}: INVALID index(es) after file: {', '.join(bad)}")
                            conn.execute("INSERT INTO schema_migrations(version, checksum) VALUES (%s, %s)",
                                         (mf.version, mf.checksum))
                except MigrationError:
                    raise
                except psycopg.Error as e:
                    bad = invalid_indexes(conn)
                    extra = f"; INVALID index(es) left behind: {', '.join(bad)}" if bad else ""
                    raise MigrationError(f"{mf.version}: {e.__class__.__name__}: {e}".strip() + extra) from e
                applied_now.append(mf.version)
                log(f"applied {mf.version}")
        finally:
            conn.execute(f"SELECT pg_advisory_unlock({ADVISORY_KEY_SQL})")
    return applied_now
