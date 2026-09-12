"""Server-side user-data cryptography primitives.

This package contains dependency-free cryptographic helpers: AES-GCM payload
encryption, RSA transport-key wrapping, and an optional Linux keyring stash for
reload recovery. HTTP exposure, identity protocols, key derivation, and account
authorization remain product responsibilities.
"""

from .aes import AesGcmError, aes_gcm_encrypt, aes_gcm_decrypt
from .transport import (
    TransportError,
    decrypt_with_transport_priv,
    public_key_pem,
    reset_transport_cache,
)

__all__ = [
    "AesGcmError",
    "aes_gcm_encrypt",
    "aes_gcm_decrypt",
    "TransportError",
    "decrypt_with_transport_priv",
    "public_key_pem",
    "reset_transport_cache",
]
