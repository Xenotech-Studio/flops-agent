"""Generic "encrypt named fields of a dict record" envelope crypto.

Relationship to aes.py: this layer doesn't touch nonces/tags — it only decides
which fields of a dict get replaced by their ciphertext form. Callers supply
an explicit list of (plain_key, ciphertext_key) pairs; this module has no
opinion on what a field is called or what the record represents. Whose field,
whose key, is entirely up to the caller — that's the "keys only travel as
explicit parameters" shape docs/TODO.md's crypto item asks for.

Each field gets its own nonce/tag: a broken field never drags others down.
Ciphertext lands as a base64 string (JSON / Redis / SQL text column friendly).

The three functions correspond to three call sites in a typical read/write
cycle:
- encrypt_fields_for_storage: plaintext -> ciphertext, called before a write
- decrypt_fields_for_use: ciphertext -> plaintext, called before use
- merge_preexisting_ciphertext: defensive ratchet — see its own docstring
"""

from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional, Tuple

from .aes import aes_gcm_decrypt, aes_gcm_encrypt


FieldPair = Tuple[str, str]

# Ciphertext length threshold used to recognize "the encrypted form of an
# empty/null payload" (see merge_preexisting_ciphertext). AES-GCM-256 over
# json.dumps(None) == "null" produces 12B nonce + 4B ct + 16B tag -> 44 base64
# chars; []/""/{} land in the same range. Real content (a tool_calls array, a
# paragraph of reasoning) encrypts to far more than 100 base64 chars. 60 is a
# safe cutoff between "null-encrypt" and "has actual content".
DEFAULT_NULL_LIKE_CT_MAX_LEN = 60


def _encrypt_one_field(value: Any, key: bytes) -> str:
    payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
    blob = aes_gcm_encrypt(payload, key)
    return base64.b64encode(blob).decode("ascii")


def _decrypt_one_field(blob_b64: str, key: bytes) -> Any:
    blob = base64.b64decode(blob_b64)
    plaintext = aes_gcm_decrypt(blob, key)
    return json.loads(plaintext.decode("utf-8"))


def encrypt_fields_for_storage(
    record: Dict[str, Any],
    key: Optional[bytes],
    fields: List[FieldPair],
) -> Dict[str, Any]:
    """Return a new dict with the protected fields in ``fields`` moved to
    their ciphertext counterpart.

    ``key`` may be None — in that case only the "already encrypted / both
    present / both missing" cases can be handled; "plaintext pending
    encryption" silently keeps the plaintext as-is (see the branch comment
    below; it does not raise).
    """
    if not isinstance(record, dict):  # pyright: ignore[reportUnnecessaryIsInstance] -- the type annotation is a contract, this guards misuse at runtime
        return record
    out = dict(record)
    for plain_key, ct_key in fields:
        plain_in = plain_key in out
        ct_in = ct_key in out
        if ct_in and plain_in:
            # Both present: ciphertext is canonical, drop the plaintext
            # sentinel (typical case: a read-then-write round trip).
            out.pop(plain_key, None)
        elif ct_in and not plain_in:
            continue  # already encrypted, nothing to do
        elif not plain_in:
            continue  # neither present, nothing to do
        else:
            # plain_in and not ct_in: needs encrypting
            if key is None:
                # No key, but don't raise either — this lets callers that
                # need to round-trip a record with no key available (and no
                # intent to touch its content) pass through cleanly. The
                # plaintext is written back unchanged: no new plaintext
                # leakage, since it was already plaintext.
                continue
            value = out.pop(plain_key)
            out[ct_key] = _encrypt_one_field(value, key)
    return out


def _looks_like_null_encrypt(ct: Any, max_len: int) -> bool:
    """Heuristic: a base64 ciphertext shorter than max_len is almost always
    the encrypted form of a JSON empty value (null/[]/""/{}/0); real content
    encrypts to something much longer."""
    return isinstance(ct, str) and len(ct) <= max_len


def merge_preexisting_ciphertext(
    stored: Dict[str, Any],
    existing: Dict[str, Any],
    fields: List[FieldPair],
    *,
    null_like_ct_max_len: int = DEFAULT_NULL_LIKE_CT_MAX_LEN,
) -> Dict[str, Any]:
    """Defensive ratchet: never let a previously stored *substantial*
    ciphertext be overwritten and lost.

    Guards against two upstream write-path bugs:
    1. ``stored`` has neither ciphertext nor plaintext (or plaintext=None) —
       an old code path — carry the ciphertext forward from ``existing``
       unchanged.
    2. ``stored`` has a ciphertext, but it's short enough to look like
       encrypt(null/[]/""), while ``existing`` has a clearly longer
       ciphertext — treat this as this write having clobbered real content
       with a null-encrypt, and preserve the ``existing`` one.

    A caller that actively supplies a non-empty plaintext still passes the
    ratchet — that's a genuine content update.
    """
    if not isinstance(stored, dict) or not isinstance(existing, dict):  # pyright: ignore[reportUnnecessaryIsInstance] -- the type annotation is a contract, this guards misuse at runtime
        return stored
    out = dict(stored)
    for plain_key, ct_key in fields:
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
            continue  # caller is providing fresh content; let encrypt produce a new ct.
        new_ct = out.get(ct_key)
        if new_ct:
            # There's already a stored ct — decide whether it should be
            # overridden by the ratchet: only when the new one is short
            # enough to look like a null-encrypt and the old one is
            # substantially longer.
            if _looks_like_null_encrypt(new_ct, null_like_ct_max_len) and not _looks_like_null_encrypt(
                old_ct, null_like_ct_max_len
            ):
                out[ct_key] = old_ct
                out.pop(plain_key, None)
            continue  # otherwise the new ct is in the same ballpark as the old one; let the caller's write stand
        # Neither a new ct nor substantial plaintext: carry the old one forward.
        out[ct_key] = old_ct
        out.pop(plain_key, None)
    return out


def decrypt_fields_for_use(
    record: Dict[str, Any],
    key: bytes,
    fields: List[FieldPair],
) -> Dict[str, Any]:
    """Decrypt ciphertext fields back onto their plaintext field names. A
    single field's failure produces a sentinel value and doesn't affect the
    others."""
    if not isinstance(record, dict):  # pyright: ignore[reportUnnecessaryIsInstance] -- the type annotation is a contract, this guards misuse at runtime
        return record
    out = dict(record)
    for plain_key, ct_key in fields:
        if ct_key not in out:
            continue
        blob_b64 = out.pop(ct_key)
        try:
            out[plain_key] = _decrypt_one_field(blob_b64, key)
        except Exception as e:
            out[plain_key] = f"[encrypted {plain_key} — decrypt failed: {type(e).__name__}]"
    return out


__all__ = [
    "FieldPair",
    "DEFAULT_NULL_LIKE_CT_MAX_LEN",
    "encrypt_fields_for_storage",
    "decrypt_fields_for_use",
    "merge_preexisting_ciphertext",
]
