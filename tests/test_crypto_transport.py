"""Unit tests for crypto.transport's explicit transport-key dependency."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, rsa  # noqa: E402

from flops_agent.crypto.transport import (  # noqa: E402
    TransportError,
    decrypt_with_transport_priv,
    encrypt_with_transport_pub,
    public_key_pem,
    transport_key_from_pem,
)


def _rsa_pem(key_size: int) -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


_VALID_PEM = _rsa_pem(2048)


# ── transport_key_from_pem validates only caller-supplied bytes ─────────────

def test_wrong_key_type_rejected():
    ec_key = ec.generate_private_key(ec.SECP256R1())
    pem = ec_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    try:
        transport_key_from_pem(pem)
        assert False, "expected TransportError for non-RSA key"
    except TransportError as exc:
        assert "RSA" in str(exc)
    print("test_wrong_key_type_rejected OK")


def test_small_key_rejected():
    try:
        transport_key_from_pem(_rsa_pem(1024))
        assert False, "expected TransportError for undersized key"
    except TransportError as exc:
        assert "too small" in str(exc)
    print("test_small_key_rejected OK")


def test_invalid_pem_rejected():
    try:
        transport_key_from_pem(b"not a PEM")
        assert False, "expected TransportError for invalid PEM"
    except TransportError as exc:
        assert "invalid transport PEM" in str(exc)
    print("test_invalid_pem_rejected OK")


# ── Every operation takes its dependency explicitly ─────────────────────────

def test_encrypt_decrypt_roundtrip():
    transport_key = transport_key_from_pem(_VALID_PEM)
    assert "BEGIN PUBLIC KEY" in public_key_pem(transport_key)

    plaintext = b"a wrapped conversation key"
    ciphertext = encrypt_with_transport_pub(transport_key, plaintext)
    assert ciphertext != plaintext
    assert decrypt_with_transport_priv(transport_key, ciphertext) == plaintext
    print("test_encrypt_decrypt_roundtrip OK")


def test_keys_are_independent_values_without_process_global_state():
    left = transport_key_from_pem(_rsa_pem(2048))
    right = transport_key_from_pem(_rsa_pem(2048))
    plaintext = b"a wrapped conversation key"

    assert public_key_pem(left) != public_key_pem(right)
    left_ciphertext = encrypt_with_transport_pub(left, plaintext)
    right_ciphertext = encrypt_with_transport_pub(right, plaintext)
    assert decrypt_with_transport_priv(left, left_ciphertext) == plaintext
    assert decrypt_with_transport_priv(right, right_ciphertext) == plaintext
    try:
        decrypt_with_transport_priv(right, left_ciphertext)
        assert False, "expected TransportError for a different key"
    except TransportError:
        pass
    print("test_keys_are_independent_values_without_process_global_state OK")


_TESTS = [value for name, value in sorted(globals().items()) if name.startswith("test_")]

if __name__ == "__main__":
    for test in _TESTS:
        test()
    print(f"\nALL {len(_TESTS)} TRANSPORT TESTS PASSED")
