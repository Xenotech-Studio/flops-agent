"""Session-persistence seam for the framework.

The chat_v2 agent loop reads and writes a composite session state (messages,
compactions, and root metadata) through ``ConversationStore``. Products inject
their own backend, such as a Redis cache-aside plus SQLite source-of-truth
dual-write implementation. The protocol is addressed by ``(user_id,
conversation_id)`` and depends only on ``typing``.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, runtime_checkable


@runtime_checkable
class ConversationStore(Protocol):
    """Session read/write seam. Methods are synchronous."""

    def load(self, user_id: str, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Load the composite session dict, or ``None`` when it does not exist."""
        ...

    def save(
        self,
        user_id: str,
        conversation_id: str,
        conversation: Dict[str, Any],
        *,
        unchanged_prefix_len: Optional[int] = None,
    ) -> None:
        """Save the composite session state.

        ``unchanged_prefix_len`` optionally asserts that ``messages[0:K]`` is
        unchanged, allowing an implementation to use its prefix fast path.
        ``None`` preserves the legacy append detection/full rewrite behavior.
        """
        ...

    def merge_meta(self, conversation: Dict[str, Any], user_id: str, conversation_id: str) -> None:
        """Merge session-level metadata into ``conversation`` before saving."""
        ...


__all__ = ["ConversationStore"]
