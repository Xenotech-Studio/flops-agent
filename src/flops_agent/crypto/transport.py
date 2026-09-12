"""Server transport keypair helpers for wrapping conversation keys.

The private key is loaded from ``TRANSPORT_PRIVATE_KEY_PATH`` or the default
path. The public PEM is derived from that private key to prevent key drift.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


DEFAULT_PRIV_PATH = Path("/home/ubuntu/secrets/transport.priv")
ENV_VAR = "TRANSPORT_PRIVATE_KEY_PATH"


class TransportError(RuntimeError):
    """The common error type for transport-key operations."""


def _resolve_priv_path() -> Path:
    env_path = os.getenv(ENV_VAR)
    if env_path:
        return Path(env_path).expanduser().resolve()
    return DEFAULT_PRIV_PATH


@lru_cache(maxsize=1)
def _load_private_key() -> rsa.RSAPrivateKey:
    path = _resolve_priv_path()
    if not path.exists():
        raise TransportError(
            f"transport private key not found at {path}; "
            f"set {ENV_VAR} or generate via "
            f"`openssl genrsa -out {path} 4096`"
        )
    pem = path.read_bytes()
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TransportError(f"transport key at {path} is not RSA")
    if key.key_size < 2048:
        raise TransportError(f"transport key too small ({key.key_size} bits)")
    return key


@lru_cache(maxsize=1)
def public_key_pem() -> str:
    """Return the cached PEM public key derived from the private key."""
    priv = _load_private_key()
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return pub_pem.decode("ascii")


def decrypt_with_transport_priv(ciphertext: bytes) -> bytes:
    """Decrypt an RSA-OAEP-SHA256 wrapped conversation key."""
    if not isinstance(ciphertext, (bytes, bytearray)):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TransportError("ciphertext must be bytes")
    priv = _load_private_key()
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
    priv = _load_private_key()
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


def reset_transport_cache() -> None:
    """Clear cached key material after rotation."""
    _load_private_key.cache_clear()
    public_key_pem.cache_clear()


__all__ = [
    "TransportError",
    "decrypt_with_transport_priv",
    "encrypt_with_transport_pub",
    "public_key_pem",
    "reset_transport_cache",
]
