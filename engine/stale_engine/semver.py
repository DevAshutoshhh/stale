"""Minimal semver for Node release strings like "v24.22.1"."""

from __future__ import annotations

import re

_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def parse(version: str) -> tuple[int, int, int]:
    m = _RE.match(version.strip())
    if not m:
        raise ValueError(f"not a semver: {version!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def major(version: str) -> int:
    return parse(version)[0]


def lt(a: str, b: str) -> bool:
    return parse(a) < parse(b)
