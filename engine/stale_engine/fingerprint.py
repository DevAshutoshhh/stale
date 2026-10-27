"""Evidence fingerprints (SRS §6.3): select → normalise → RFC 8785 → sha256.

Fingerprints only decide whether an expensive re-proof may be skipped. They never decide
staleness; rules do.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

import rfc8785

_HEX64 = re.compile(r"^(sha256:)?[0-9a-fA-F]{64}$")
_MISSING = object()


def row_bucket(reltuples: float | int | None) -> float:
    """floor(log10(max(reltuples, 1)) * 4) / 4 — natural growth stays in one bucket."""
    return math.floor(math.log10(max(float(reltuples or 0), 1.0)) * 4) / 4


def _select(obj: Any, parts: list[str], under_wildcard: bool = False) -> Any:
    """Below a `*`, keys are kept so key-based normalisation (e.g. `reltuples`) still applies."""
    if not parts:
        return obj
    head, rest = parts[0], parts[1:]
    if not isinstance(obj, dict):
        return _MISSING
    if head == "*":
        out = {}
        for k in sorted(obj):
            v = _select(obj[k], rest, True)
            if v is not _MISSING:
                out[k] = v
        return out
    if head not in obj:
        return _MISSING
    v = _select(obj[head], rest, under_wildcard)
    return {head: v} if under_wildcard and v is not _MISSING else v


def select(evidence: dict[str, Any], include: list[str]) -> dict[str, Any]:
    """Pick the dotted paths in `include`. `*` matches every key at that level. Missing → null."""
    out: dict[str, Any] = {}
    for path in include:
        v = _select(evidence, path.split("."))
        out[path] = None if v is _MISSING else v
    return out


def normalise(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k == "reltuples":
                out["reltuples_bucket"] = row_bucket(v)
            else:
                out[k] = normalise(v, k)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [normalise(v) for v in value]
        # Sets of scalars (SPKI lists, rule lists) are order-free: sort and dedupe.
        if all(isinstance(i, (str, int, float, bool)) or i is None for i in items):
            return sorted(set(items), key=lambda x: (str(type(x)), x))
        return items
    if isinstance(value, str) and _HEX64.match(value):
        return value.lower()
    if key == "reltuples":
        return row_bucket(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def fingerprint(evidence: dict[str, Any], include: list[str]) -> str:
    canonical = rfc8785.dumps(normalise(select(evidence, include)))
    return "sha256:" + hashlib.sha256(canonical).hexdigest()
