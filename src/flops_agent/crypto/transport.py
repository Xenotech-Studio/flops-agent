"""Transport-key primitives for wrapping conversation keys.

This module does not locate, read, cache, or select a private key. The
embedding application resolves its secret at its composition root, creates a
``TransportKey`` from the resulting PEM bytes, and passes that value explicitly
to each operation. This keeps deployment configuration and key lifetime
outside the kernel, and permits independent keys in the same process.
"""

from __future__ import annotations

from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class TransportError(RuntimeError):
    """The common error type for transport-key operations."""


@dataclass(frozen=True)
class TransportKey:
    """An RSA private key and its derived public PEM, supplied by a caller."""

    _private_key: rsa.RSAPrivateKey
    _public_pem: str

    @property
    def public_pem(self) -> str:
        """The PEM serialization of this key's public half."""
        return self._public_pem

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Decrypt RSA-OAEP-SHA256 ciphertext with this key."""
        return self._private_key.decrypt(
            ciphertext,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt plaintext using this key's public RSA-OAEP-SHA256 half."""
        return self._private_key.public_key().encrypt(
            plaintext,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )


def transport_key_from_pem(pem: bytes) -> TransportKey:
    """Validate PEM bytes and return an explicit transport-key dependency."""
    if not isinstance(pem, bytes):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TransportError("transport PEM must be bytes")
    try:
        private_key = serialization.load_pem_private_key(pem, password=None)
    except Exception as exc:
        raise TransportError(f"invalid transport PEM: {exc}") from exc
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise TransportError("transport key is not RSA")
    if private_key.key_size < 2048:
        raise TransportError(f"transport key too small ({private_key.key_size} bits)")

    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return TransportKey(private_key, public_pem.decode("ascii"))


def _require_transport_key(key: TransportKey) -> TransportKey:
    if not isinstance(key, TransportKey):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TransportError("transport key must be a TransportKey")
    return key


def public_key_pem(key: TransportKey) -> str:
    """Return the public PEM derived from the explicitly supplied key."""
    return _require_transport_key(key).public_pem


def decrypt_with_transport_priv(key: TransportKey, ciphertext: bytes) -> bytes:
    """Decrypt an RSA-OAEP-SHA256 wrapped conversation key with ``key``."""
    if not isinstance(ciphertext, (bytes, bytearray)):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TransportError("ciphertext must be bytes")
    try:
        return _require_transport_key(key).decrypt(bytes(ciphertext))
    except TransportError:
        raise
    except Exception as exc:
        raise TransportError(f"transport decrypt failed: {exc}") from exc


def encrypt_with_transport_pub(key: TransportKey, plaintext: bytes) -> bytes:
    """Wrap a key with ``key``'s public RSA-OAEP-SHA256 key."""
    if not isinstance(plaintext, (bytes, bytearray)):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TransportError("plaintext must be bytes")
    try:
        return _require_transport_key(key).encrypt(bytes(plaintext))
    except TransportError:
        raise
    except Exception as exc:
        raise TransportError(f"transport encrypt failed: {exc}") from exc


__all__ = [
    "TransportError",
    "TransportKey",
    "decrypt_with_transport_priv",
    "encrypt_with_transport_pub",
    "public_key_pem",
    "transport_key_from_pem",
]
