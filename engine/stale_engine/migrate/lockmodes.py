"""Statement → lock-mode classification (SRS §9.2). Unknown statements are ACCESS EXCLUSIVE."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

import pglast
from pglast import ast
from pglast.enums import AlterTableType, ConstrType, ObjectType
from pglast.stream import RawStream

ACCESS_EXCLUSIVE = "ACCESS EXCLUSIVE"
SHARE = "SHARE"
SHARE_UPDATE_EXCLUSIVE = "SHARE UPDATE EXCLUSIVE"
SHARE_ROW_EXCLUSIVE = "SHARE ROW EXCLUSIVE"
BLOCKING = {ACCESS_EXCLUSIVE, SHARE}

VOLATILE_FUNCS = {"random", "gen_random_uuid", "uuid_generate_v4", "clock_timestamp",
                  "timeofday", "nextval", "txid_current"}

_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|min)?\s*$", re.I)


@dataclass
class StatementInfo:
    sql_normalized: str
    kind: str
    table: str | None = None
    lock_mode: str | None = None
    concurrent: bool = False
    sets_lock_timeout_ms: int | None = None
    index_name: str | None = None
    if_not_exists: bool = False
    if_exists: bool = False
    rewrite: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _timeout_ms(raw: str) -> int | None:
    m = _DURATION.match(raw.strip("'\""))
    if not m:
        return None
    n, unit = float(m.group(1)), (m.group(2) or "ms").lower()
    return int(n * {"ms": 1, "s": 1000, "min": 60000}[unit])


def _const_value(node: Any) -> str:
    val = getattr(node, "val", None)
    for attr in ("sval", "ival", "fval"):
        if val is not None and hasattr(val, attr):
            return str(getattr(val, attr))
    return str(val)


def _is_volatile(expr: Any) -> bool:
    if isinstance(expr, ast.FuncCall):
        name = expr.funcname[-1].sval.lower()
        if name in VOLATILE_FUNCS:
            return True
        return any(_is_volatile(a) for a in (expr.args or ()))
    if isinstance(expr, ast.TypeCast):
        return _is_volatile(expr.arg)
    return False


def _alter_table(stmt: ast.AlterTableStmt, info: StatementInfo) -> None:
    info.table = stmt.relation.relname
    info.kind = "alter_table"
    modes: list[str] = []
    for cmd in stmt.cmds:
        st = cmd.subtype
        if st == AlterTableType.AT_AddColumn:
            volatile = any(c.contype == ConstrType.CONSTR_DEFAULT and _is_volatile(c.raw_expr)
                           for c in (cmd.def_.constraints or ()))
            info.rewrite |= volatile
            info.kind = "add_column"
            modes.append(ACCESS_EXCLUSIVE)
        elif st == AlterTableType.AT_AlterColumnType:
            info.rewrite = True  # conservative: most type changes rewrite
            info.kind = "alter_column_type"
            modes.append(ACCESS_EXCLUSIVE)
        elif st == AlterTableType.AT_AddConstraint and getattr(cmd.def_, "skip_validation", False):
            info.kind = "add_constraint_not_valid"
            modes.append(SHARE_ROW_EXCLUSIVE)
        elif st == AlterTableType.AT_ValidateConstraint:
            info.kind = "validate_constraint"
            modes.append(SHARE_UPDATE_EXCLUSIVE)
        else:
            modes.append(ACCESS_EXCLUSIVE)
    order = [ACCESS_EXCLUSIVE, SHARE_ROW_EXCLUSIVE, SHARE, SHARE_UPDATE_EXCLUSIVE]
    info.lock_mode = min(modes, key=order.index) if modes else ACCESS_EXCLUSIVE


def classify(sql: str) -> StatementInfo:
    """Classify one SQL statement."""
    stmts = pglast.parse_sql(sql)
    if len(stmts) != 1:
        raise ValueError(f"expected exactly one statement, got {len(stmts)}")
    stmt = stmts[0].stmt
    info = StatementInfo(sql_normalized=RawStream()(stmt), kind="unknown", lock_mode=ACCESS_EXCLUSIVE)

    if isinstance(stmt, ast.IndexStmt):
        info.kind = "create_index"
        info.table = stmt.relation.relname
        info.index_name = stmt.idxname
        info.concurrent = bool(stmt.concurrent)
        info.if_not_exists = bool(stmt.if_not_exists)
        info.lock_mode = SHARE_UPDATE_EXCLUSIVE if info.concurrent else SHARE
    elif isinstance(stmt, ast.DropStmt) and stmt.removeType == ObjectType.OBJECT_INDEX:
        info.kind = "drop_index"
        names = [n.sval for n in stmt.objects[0]]
        info.index_name = names[-1]
        info.concurrent = bool(stmt.concurrent)
        info.if_exists = bool(stmt.missing_ok)
        info.lock_mode = SHARE_UPDATE_EXCLUSIVE if info.concurrent else ACCESS_EXCLUSIVE
    elif isinstance(stmt, ast.AlterTableStmt):
        _alter_table(stmt, info)
    elif isinstance(stmt, ast.CreateStmt):
        info.kind, info.table, info.lock_mode = "create_table", stmt.relation.relname, None
    elif isinstance(stmt, ast.VariableSetStmt):
        name = (stmt.name or "").lower()
        info.lock_mode = None
        if name == "lock_timeout":
            if stmt.args:
                info.kind = "set_lock_timeout"
                info.sets_lock_timeout_ms = _timeout_ms(_const_value(stmt.args[0]))
            else:  # RESET lock_timeout / SET lock_timeout TO DEFAULT
                info.kind = "reset_lock_timeout"
        else:
            info.kind = "set"
    elif isinstance(stmt, (ast.InsertStmt, ast.UpdateStmt, ast.DeleteStmt, ast.SelectStmt)):
        info.kind = "dml"
        rel = getattr(stmt, "relation", None)
        info.table = getattr(rel, "relname", None)
        info.lock_mode = "ROW EXCLUSIVE"
    return info
