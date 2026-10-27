from cryptography.hazmat.primitives.asymmetric import ec, rsa

from stale_engine import weakkeys

from .conftest import ec_x, random_ec_x


def test_k4_p256_is_weak():
    assert weakkeys.is_weak_ec_x("prime256v1", ec_x(4))


def test_k4_p384_is_weak():
    x = ec.derive_private_key(4, ec.SECP384R1()).public_key().public_numbers().x
    assert weakkeys.is_weak_ec_x("secp384r1", x)
    assert weakkeys.is_weak_ec_x("P-384", f"{x:096x}")


def test_table_bounds():
    assert weakkeys.is_weak_ec_x("prime256v1", ec_x(1))
    assert weakkeys.is_weak_ec_x("prime256v1", ec_x(2**16 - 1))
    assert not weakkeys.is_weak_ec_x("prime256v1", ec_x(2**16))


def test_random_key_not_weak():
    for _ in range(5):
        assert not weakkeys.is_weak_ec_x("prime256v1", random_ec_x())


def test_negated_point_shares_x():
    # -kG has the same x as kG, so checking x alone also catches it.
    assert weakkeys.is_weak_ec_x("secp256r1", ec_x(4))


def test_unknown_curve_and_bad_input():
    assert not weakkeys.is_weak_ec_x("secp521r1", ec_x(4))
    assert not weakkeys.is_weak_ec_x("prime256v1", b"\x00" * 31)
    assert not weakkeys.is_weak_ec_x("prime256v1", 2**300)


def test_is_weak_key_shapes():
    assert weakkeys.is_weak_key({"type": "ec", "curve": "prime256v1", "x": ec_x(4)})[0]
    assert not weakkeys.is_weak_key({"type": "ec", "curve": "prime256v1", "x": random_ec_x()})[0]
    assert weakkeys.is_weak_key({"type": "rsa", "bits": 1024}) == (True, "RSA modulus is 1024 bits (< 2048)")
    assert not weakkeys.is_weak_key({"type": "rsa", "bits": 2048})[0]
    assert not weakkeys.is_weak_key({"type": "ed25519"})[0]


def test_rsa_bits_from_real_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    assert weakkeys.is_weak_key({"type": "rsa", "bits": key.key_size})[0]


def test_table_file_is_sorted_and_sized():
    table = weakkeys._table("prime256v1")
    assert len(table) == (2**16 - 1) * 32
    recs = [table[i:i + 32] for i in range(0, 32 * 200, 32)]
    assert recs == sorted(recs)
