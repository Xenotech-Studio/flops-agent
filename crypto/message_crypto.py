"""Encryption and decryption filters for persisted conversation messages.

Protected fields are stored independently, each with its own AES-GCM nonce and
authentication tag. The functions are idempotent for read-modify-save paths:
existing ciphertext remains canonical, while unencrypted legacy fields may pass
through until a request provides a conversation key.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional, Tuple

from .aes import aes_gcm_decrypt, aes_gcm_encrypt


_ENCRYPTED_FIELDS: List[Tuple[str, str]] = [
    ("content", "content_ciphertext"),
    ("tool_calls", "tool_calls_ciphertext"),
    ("reasoning_content", "reasoning_content_ciphertext"),
    ("reasoning_seconds", "reasoning_seconds_ciphertext"),
]


def _encrypt_one_field(value: Any, k_conv: bytes) -> str:
    payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
    blob = aes_gcm_encrypt(payload, k_conv)
    return base64.b64encode(blob).decode("ascii")


def _decrypt_one_field(blob_b64: str, k_conv: bytes) -> Any:
    blob = base64.b64decode(blob_b64)
    plaintext = aes_gcm_decrypt(blob, k_conv)
    return json.loads(plaintext.decode("utf-8"))


def encrypt_message_for_storage(msg: Dict[str, Any], k_conv: Optional[bytes]) -> Dict[str, Any]:
    """Return a copy with protected plaintext moved to ciphertext fields."""
    if not isinstance(msg, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
        return msg
    out = dict(msg)
    for plain_key, ct_key in _ENCRYPTED_FIELDS:
        plain_in = plain_key in out
        ct_in = ct_key in out
        if ct_in and plain_in:
            # Ciphertext is canonical during a read-modify-save round trip.
            out.pop(plain_key, None)
        elif ct_in and not plain_in:
            # The field is already encrypted.
            continue
        elif not plain_in:
            continue
        else:
            # Plaintext without ciphertext needs a key before encryption.
            if k_conv is None:
                # Legacy plaintext may safely survive a metadata-only round trip;
                # a later request with K_conv will encrypt it.
                continue
            value = out.pop(plain_key)
            out[ct_key] = _encrypt_one_field(value, k_conv)
    return out


# Ciphertext length threshold for "null/empty payload" detection.
# AES-GCM-256 of json.dumps(None) = "null" (4B) → 4 + 16 tag + 12 nonce
#   = 32B raw → 44 char base64.
# AES-GCM-256 of json.dumps([]) / "" / {} → similar (≤ 44 chars).
# Real content like a tool_calls array or reasoning paragraph encrypts to
# > 100 chars after base64. 60 is a safe threshold between "null-encrypt"
# and "meaningful".
_NULL_LIKE_CT_MAX_LEN = 60


def _looks_like_null_encrypt(ct: Any) -> bool:
    """Heuristic: a ciphertext under 60 base64 chars is almost certainly an
    encryption of one of the JSON empty values (null / [] / "" / {} / 0).
    Real tool_calls / reasoning_content encrypts to much more."""
    return isinstance(ct, str) and len(ct) <= _NULL_LIKE_CT_MAX_LEN


def merge_preexisting_ciphertext(
    stored: Dict[str, Any],
    existing: Dict[str, Any],
) -> Dict[str, Any]:
    """Prevent meaningful existing ciphertext from being replaced by empty data."""
    if not isinstance(stored, dict) or not isinstance(existing, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
        return stored
    out = dict(stored)
    for plain_key, ct_key in _ENCRYPTED_FIELDS:
        old_ct = existing.get(ct_key)
        if not old_ct:
            continue
        plain_val = out.get(plain_key)
        plain_has_value = (
            plain_val is not None
            and plain_val != ""
            and plain_val != []
            and plain_val != {}
        )
        if plain_has_value:
            # caller is providing fresh content; let encrypt produce a new ct.
            continue
        new_ct = out.get(ct_key)
        if new_ct:
            # Preserve substantial existing ciphertext over a null-like update.
            if _looks_like_null_encrypt(new_ct) and not _looks_like_null_encrypt(old_ct):
                out[ct_key] = old_ct
                out.pop(plain_key, None)
            continue
        # No new ciphertext or substantive plaintext: retain existing data.
        out[ct_key] = old_ct
        out.pop(plain_key, None)
    return out


def decrypt_message_for_use(msg: Dict[str, Any], k_conv: bytes) -> Dict[str, Any]:
    """Decrypt ciphertext fields independently into their plaintext names."""
    if not isinstance(msg, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
        return msg
    out = dict(msg)
    for plain_key, ct_key in _ENCRYPTED_FIELDS:
        if ct_key not in out:
            continue
        blob_b64 = out.pop(ct_key)
        try:
            out[plain_key] = _decrypt_one_field(blob_b64, k_conv)
        except Exception as e:
            out[plain_key] = f"[encrypted {plain_key} — decrypt failed: {type(e).__name__}]"
    return out


__all__ = [
    "encrypt_message_for_storage",
    "decrypt_message_for_use",
    "merge_preexisting_ciphertext",
]
