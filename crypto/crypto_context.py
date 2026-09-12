"""Request-scoped conversation and agent encryption keys.

Context variables isolate keys to the current task and synchronous call stack.
Callers that create a separate task or executor context must propagate keys
explicitly when required.
"""

from __future__ import annotations

import contextvars
from typing import Optional

_active_kconv: contextvars.ContextVar[Optional[bytes]] = contextvars.ContextVar(
    "flops_active_kconv",
    default=None,
)

# K_agent is a long-lived per-agent key and follows the same request scoping
# rules as the per-conversation K_conv key.
_active_kagent: contextvars.ContextVar[Optional[bytes]] = contextvars.ContextVar(
    "flops_active_kagent",
    default=None,
)


def set_active_kconv(kconv_bytes: bytes) -> "contextvars.Token[Optional[bytes]]":
    """Set K_conv in this context and return its reset token."""
    if not isinstance(kconv_bytes, (bytes, bytearray)) or len(kconv_bytes) != 32:  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ValueError("K_conv must be 32 bytes")
    return _active_kconv.set(bytes(kconv_bytes))


def get_active_kconv() -> Optional[bytes]:
    """Return K_conv from the current context, if present."""
    return _active_kconv.get()


def clear_active_kconv(token: Optional["contextvars.Token[Optional[bytes]]"] = None) -> None:
    """Reset K_conv at a request boundary; without a token, clear it."""
    if token is not None:
        try:
            _active_kconv.reset(token)
            return
        except ValueError:
            # The token was already reset in this context; clear as a fallback.
            pass
    _active_kconv.set(None)


def set_active_kagent(kagent_bytes: bytes) -> "contextvars.Token[Optional[bytes]]":
    if not isinstance(kagent_bytes, (bytes, bytearray)) or len(kagent_bytes) != 32:  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ValueError("K_agent must be 32 bytes")
    return _active_kagent.set(bytes(kagent_bytes))


def get_active_kagent() -> Optional[bytes]:
    return _active_kagent.get()


def clear_active_kagent(token: Optional["contextvars.Token[Optional[bytes]]"] = None) -> None:
    if token is not None:
        try:
            _active_kagent.reset(token)
            return
        except ValueError:
            pass
    _active_kagent.set(None)


__all__ = [
    "set_active_kconv",
    "get_active_kconv",
    "clear_active_kconv",
    "set_active_kagent",
    "get_active_kagent",
    "clear_active_kagent",
]
