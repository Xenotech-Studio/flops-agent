"""Server transport keypair helpers for wrapping conversation keys.

The kernel has no opinion on where the transport private key lives — no
default path, no home-directory convention, no filesystem access at all.
Key provisioning is the embedding application's responsibility: it obtains
the PEM bytes from wherever is appropriate for its deployment (an env var
pointing at a file it reads itself, a secrets manager, a KMS-backed
unwrap, ...) and calls ``configure_transport_privkey_pem()`` once at
startup with the resulting bytes. Every crypto function in this module
then operates on that configured key; none of them touch ``os.environ``
or the filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class TransportError(RuntimeError):
    """The common error type for transport-key operations."""


_NOT_CONFIGURED_MSG = (
    "transport private key not configured — call "
    "configure_transport_privkey_pem() at startup with PEM bytes from "
    "your config/env"
)


@dataclass
class _TransportKeyHolder:
    private_key: Optional[rsa.RSAPrivateKey] = None
    public_pem: Optional[str] = None


_holder = _TransportKeyHolder()


def configure_transport_privkey_pem(pem: bytes) -> None:
    """Configure the transport private key from PEM bytes.

    Call this once at process startup, before any other function in this
    module is used. The caller (the embedding application) is responsible
    for obtaining ``pem`` from its own configuration source.
    """
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TransportError("transport key is not RSA")
    if key.key_size < 2048:
        raise TransportError(f"transport key too small ({key.key_size} bits)")
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    _holder.private_key = key
    _holder.public_pem = pub_pem.decode("ascii")


def reset_transport_key() -> None:
    """Clear the configured key material, e.g. before rotation or in tests."""
    _holder.private_key = None
    _holder.public_pem = None


def _require_key() -> rsa.RSAPrivateKey:
    if _holder.private_key is None:
        raise TransportError(_NOT_CONFIGURED_MSG)
    return _holder.private_key


def public_key_pem() -> str:
    """Return the PEM public key derived from the configured private key."""
    if _holder.public_pem is None:
        raise TransportError(_NOT_CONFIGURED_MSG)
    return _holder.public_pem


def decrypt_with_transport_priv(ciphertext: bytes) -> bytes:
    """Decrypt an RSA-OAEP-SHA256 wrapped conversation key."""
    if not isinstance(ciphertext, (bytes, bytearray)):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TransportError("ciphertext must be bytes")
    priv = _require_key()
    try:
        plaintext = priv.decrypt(
            bytes(ciphertext),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    except Exception as e:
        raise TransportError(f"transport decrypt failed: {e}") from e
    return plaintext


def encrypt_with_transport_pub(plaintext: bytes) -> bytes:
    """Wrap a key with the transport public key using RSA-OAEP-SHA256."""
    if not isinstance(plaintext, (bytes, bytearray)):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TransportError("plaintext must be bytes")
    priv = _require_key()
    pub = priv.public_key()
    try:
        return pub.encrypt(
            bytes(plaintext),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    except Exception as e:
        raise TransportError(f"transport encrypt failed: {e}") from e


__all__ = [
    "TransportError",
    "configure_transport_privkey_pem",
    "decrypt_with_transport_priv",
    "encrypt_with_transport_pub",
    "public_key_pem",
    "reset_transport_key",
]
