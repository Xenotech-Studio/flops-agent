"""Unit tests for crypto.transport -- the kernel must never read key material
from disk or env; it only ever operates on PEM bytes handed to it explicitly
by configure_transport_privkey_pem().
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, rsa  # noqa: E402

from flops_agent.crypto.transport import (  # noqa: E402
    TransportError,
    configure_transport_privkey_pem,
    decrypt_with_transport_priv,
    encrypt_with_transport_pub,
    public_key_pem,
    reset_transport_key,
)


def _rsa_pem(key_size: int) -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


_VALID_PEM = _rsa_pem(2048)


def setup_function(_fn) -> None:
    reset_transport_key()


def teardown_function(_fn) -> None:
    reset_transport_key()


# ── Not configured: fail closed, never touch the filesystem ─────────────────

def test_unconfigured_raises_with_actionable_message():
    for fn in (public_key_pem, lambda: decrypt_with_transport_priv(b"x"), lambda: encrypt_with_transport_pub(b"x")):
        try:
            fn()
            assert False, "expected TransportError"
        except TransportError as e:
            assert "configure_transport_privkey_pem" in str(e)
    print("test_unconfigured_raises_with_actionable_message OK")


# ── configure_transport_privkey_pem validates what it's handed ──────────────

def test_wrong_key_type_rejected():
    ec_key = ec.generate_private_key(ec.SECP256R1())
    pem = ec_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    try:
        configure_transport_privkey_pem(pem)
        assert False, "expected TransportError for non-RSA key"
    except TransportError as e:
        assert "RSA" in str(e)
    print("test_wrong_key_type_rejected OK")


def test_small_key_rejected():
    pem = _rsa_pem(1024)
    try:
        configure_transport_privkey_pem(pem)
        assert False, "expected TransportError for undersized key"
    except TransportError as e:
        assert "too small" in str(e)
    print("test_small_key_rejected OK")


# ── Happy path: configure once, use everywhere ───────────────────────────────

def test_encrypt_decrypt_roundtrip():
    configure_transport_privkey_pem(_VALID_PEM)
    pub_pem = public_key_pem()
    assert "BEGIN PUBLIC KEY" in pub_pem

    plaintext = b"a wrapped conversation key"
    ciphertext = encrypt_with_transport_pub(plaintext)
    assert ciphertext != plaintext
    assert decrypt_with_transport_priv(ciphertext) == plaintext
    print("test_encrypt_decrypt_roundtrip OK")


def test_reset_clears_configured_key():
    configure_transport_privkey_pem(_VALID_PEM)
    public_key_pem()  # does not raise

    reset_transport_key()

    try:
        public_key_pem()
        assert False, "expected TransportError after reset"
    except TransportError:
        pass
    print("test_reset_clears_configured_key OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in _TESTS:
        setup_function(t)
        t()
    print(f"\nALL {len(_TESTS)} TRANSPORT TESTS PASSED")
