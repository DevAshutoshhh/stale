"""Weak-anchor detection for rule R4 (SRS §9.1).

EC: a public point whose x-coordinate equals (k·G).x for 1 <= k < 2^16 on P-256 or P-384.
Checking x alone also catches −kG. RSA: modulus shorter than 2048 bits.

The x-coordinates are precomputed once into sorted fixed-width binary files
(`data/weak_ec_x_<curve>.bin`) and looked up by binary search.
`python -m stale_engine.weakkeys build` generates them; lookups build them lazily if missing.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ec

K_MAX = 2**16  # exclusive
DATA_DIR = Path(__file__).parent / "data"

CURVES: dict[str, tuple[ec.EllipticCurve, int]] = {
    "prime256v1": (ec.SECP256R1(), 32),
    "secp384r1": (ec.SECP384R1(), 48),
}
ALIASES = {
    "prime256v1": "prime256v1", "secp256r1": "prime256v1", "p-256": "prime256v1", "p256": "prime256v1",
    "secp384r1": "secp384r1", "p-384": "secp384r1", "p384": "secp384r1",
}
MIN_RSA_BITS = 2048


def canonical_curve(name: str) -> str | None:
    return ALIASES.get(name.strip().lower())


def table_path(curve: str) -> Path:
    return DATA_DIR / f"weak_ec_x_{curve}.bin"


def build(curve: str, k_max: int = K_MAX) -> Path:
    crv, width = CURVES[curve]
    xs = [
        ec.derive_private_key(k, crv).public_key().public_numbers().x.to_bytes(width, "big")
        for k in range(1, k_max)
    ]
    xs.sort()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = table_path(curve)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(b"".join(xs))
    tmp.replace(path)
    return path


@lru_cache(maxsize=None)
def _table(curve: str) -> bytes:
    path = table_path(curve)
    _, width = CURVES[curve]
    if not path.exists() or path.stat().st_size != (K_MAX - 1) * width:
        build(curve)
    return path.read_bytes()


def is_weak_ec_x(curve: str, x: int | str | bytes) -> bool:
    c = canonical_curve(curve)
    if c is None:
        return False
    _, width = CURVES[c]
    if isinstance(x, str):
        x = int(x, 16)
    if isinstance(x, int):
        if x < 0 or x.bit_length() > width * 8:
            return False
        x = x.to_bytes(width, "big")
    if len(x) != width:
        return False
    table = _table(c)
    lo, hi = 0, len(table) // width
    while lo < hi:
        mid = (lo + hi) // 2
        rec = table[mid * width:(mid + 1) * width]
        if rec < x:
            lo = mid + 1
        elif rec > x:
            hi = mid
        else:
            return True
    return False


def is_weak_key(key: dict) -> tuple[bool, str]:
    """`key` is the runtime-report shape: {"type":"ec","curve":..,"x":hex} or {"type":"rsa","bits":n}."""
    ktype = str(key.get("type", "")).lower()
    if ktype == "ec":
        curve = str(key.get("curve", ""))
        if canonical_curve(curve) and key.get("x") and is_weak_ec_x(curve, key["x"]):
            return True, f"EC {curve} public x equals (k*G).x for some 1 <= k < 2^16"
        return False, ""
    if ktype == "rsa":
        bits = int(key.get("bits") or 0)
        if 0 < bits < MIN_RSA_BITS:
            return True, f"RSA modulus is {bits} bits (< {MIN_RSA_BITS})"
    return False, ""


def main(argv: list[str]) -> int:
    if argv[:1] != ["build"]:
        print("usage: python -m stale_engine.weakkeys build", file=sys.stderr)
        return 2
    for curve in CURVES:
        print(f"built {build(curve)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
