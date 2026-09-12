"""flops_agent.crypto — generic cryptographic primitives.

Submodules:
- aes: AES-256-GCM helpers (fresh random nonce per call), used for payload
  and key-wrap envelopes.
- transport: RSA-4096 transport keypair (the server loads the private key;
  a client encrypts a wrapped key with the public key). Exposing the public
  key over an HTTP endpoint is the embedding product's responsibility.
- field_crypto: generic "encrypt named fields of a dict record" envelope
  crypto — the caller supplies the field-name pairs and the key explicitly;
  this module has no opinion on what a field is called.

Design principles:
- Nonces are never reused (fresh ``secrets.token_bytes(12)`` per call).
- This package holds only pure cryptographic primitives, with no dependency
  on any product module (no Redis, no web framework, no identity/session
  state).
- No "which key is currently active" runtime state lives here — request-
  scoped active-key plumbing (contextvars), hot-reload key stashing, key
  derivation, and account authorization protocols are deployment decisions
  that belong to the product embedding this package, not the framework
  (see ``docs/TODO.md`` item 3).
"""

from .aes import AesGcmError, aes_gcm_encrypt, aes_gcm_decrypt
from .transport import (
    TransportError,
    decrypt_with_transport_priv,
    public_key_pem,
    reset_transport_cache,
)
from .field_crypto import (
    FieldPair,
    encrypt_fields_for_storage,
    decrypt_fields_for_use,
    merge_preexisting_ciphertext,
)

__all__ = [
    "AesGcmError",
    "aes_gcm_encrypt",
    "aes_gcm_decrypt",
    "TransportError",
    "decrypt_with_transport_priv",
    "public_key_pem",
    "reset_transport_cache",
    "FieldPair",
    "encrypt_fields_for_storage",
    "decrypt_fields_for_use",
    "merge_preexisting_ciphertext",
]
