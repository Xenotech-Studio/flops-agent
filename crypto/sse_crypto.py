"""Best-effort AES-GCM wrapping for SSE data chunks.

The inner JSON payload is encrypted with the active conversation key and sent
as an ``encrypted_chunk`` frame. Plain conversations pass through unchanged.
Wrapping at the generator boundary covers every event producer without changing
individual SSE emit sites.
"""

from __future__ import annotations

import base64
import json
from typing import AsyncIterator, Optional

from .aes import aes_gcm_encrypt
from flops_agent.crypto.crypto_context import get_active_kconv


_DATA_PREFIX = "data: "
_DATA_SUFFIX = "\n\n"


def _wrap_sse_chunk_with_kconv(chunk: str, kconv: bytes) -> str:
    """Encrypt a valid JSON SSE data frame; pass any other frame through."""
    if not isinstance(chunk, str):  # pyright: ignore[reportUnnecessaryIsInstance]
        return chunk
    if not chunk.startswith(_DATA_PREFIX):
        return chunk
    body = chunk[len(_DATA_PREFIX):]
    if body.endswith(_DATA_SUFFIX):
        body = body[: -len(_DATA_SUFFIX)]
    body = body.strip()
    if not body:
        return chunk
    # Confirm that this is a JSON SSE data frame before wrapping it.
    try:
        json.loads(body)
    except Exception:
        return chunk
    blob = aes_gcm_encrypt(body.encode("utf-8"), kconv)
    ct_b64 = base64.b64encode(blob).decode("ascii")
    wrapper = json.dumps(
        {"type": "encrypted_chunk", "ciphertext": ct_b64},
        ensure_ascii=False,
    )
    return f"{_DATA_PREFIX}{wrapper}{_DATA_SUFFIX}"


async def maybe_encrypt_sse_stream(
    inner: AsyncIterator[str],
    *,
    force_kconv: Optional[bytes] = None,
) -> AsyncIterator[str]:
    """Wrap chunks when a conversation key is available.

    ``force_kconv`` is an explicit fallback for task boundaries that do not
    retain the request context variable.
    """
    kconv = force_kconv if force_kconv is not None else get_active_kconv()
    if kconv is None:
        async for chunk in inner:
            yield chunk
        return
    async for chunk in inner:
        yield _wrap_sse_chunk_with_kconv(chunk, kconv)


__all__ = [
    "maybe_encrypt_sse_stream",
    "_wrap_sse_chunk_with_kconv",
]
