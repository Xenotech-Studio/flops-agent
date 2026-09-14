"""Session-persistence seam for the framework.

Products inject their own backend for a composite session state containing
messages, compactions, and root metadata. The protocol is addressed by
(owner_id, session_id) and depends only on typing.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, runtime_checkable


@runtime_checkable
class SessionStore(Protocol):
    """Session read/write seam. Methods are synchronous."""

    def load(self, owner_id: str, session_id: str) -> Optional[Dict[str, Any]]:
        """Load the composite session dict, or None when it does not exist."""
        ...

    def save(
        self,
        owner_id: str,
        session_id: str,
        session: Dict[str, Any],
        *,
        unchanged_prefix_len: Optional[int] = None,
    ) -> None:
        """Save the composite session state.

        unchanged_prefix_len optionally asserts that messages[0:K] is
        unchanged, allowing an implementation to use its prefix fast path.
        None preserves append detection or full-rewrite behavior.
        """
        ...

    def merge_meta(self, session: Dict[str, Any], owner_id: str, session_id: str) -> None:
        """Merge session-level metadata into session before saving."""
        ...


__all__ = ["SessionStore"]
