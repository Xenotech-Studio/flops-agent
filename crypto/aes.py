"""AES-256-GCM helpers.

Keys are 32 bytes and every call uses a fresh 12-byte nonce. The output is
``nonce || ciphertext || tag``. If AAD is used, callers must supply identical
bytes for encryption and decryption.
"""

from __future__ import annotations

import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


NONCE_LEN = 12
KEY_LEN = 32


class AesGcmError(RuntimeError):
    """The common error type for AES-GCM operations."""


def _check_key(key: bytes) -> None:
    if not isinstance(key, (bytes, bytearray)):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise AesGcmError("key must be bytes")
    if len(key) != KEY_LEN:
        raise AesGcmError(f"key must be {KEY_LEN} bytes (got {len(key)})")


def aes_gcm_encrypt(plaintext: bytes, key: bytes, *, aad: bytes | None = None) -> bytes:
    """Return ``nonce || ciphertext_with_tag`` using a fresh nonce."""
    if not isinstance(plaintext, (bytes, bytearray)):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise AesGcmError("plaintext must be bytes")
    _check_key(key)
    nonce = secrets.token_bytes(NONCE_LEN)
    aesgcm = AESGCM(bytes(key))
    ct = aesgcm.encrypt(nonce, bytes(plaintext), aad)
    return nonce + ct


def aes_gcm_decrypt(blob: bytes, key: bytes, *, aad: bytes | None = None) -> bytes:
    """Decrypt an ``aes_gcm_encrypt`` blob or raise ``AesGcmError``."""
    if not isinstance(blob, (bytes, bytearray)):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise AesGcmError("blob must be bytes")
    if len(blob) < NONCE_LEN + 16:
        raise AesGcmError("blob too short")
    _check_key(key)
    nonce = bytes(blob[:NONCE_LEN])
    ct = bytes(blob[NONCE_LEN:])
    aesgcm = AESGCM(bytes(key))
    try:
        return aesgcm.decrypt(nonce, ct, aad)
    except InvalidTag as e:
        raise AesGcmError("AES-GCM auth tag verification failed") from e


__all__ = [
    "AesGcmError",
    "NONCE_LEN",
    "KEY_LEN",
    "aes_gcm_encrypt",
    "aes_gcm_decrypt",
]
