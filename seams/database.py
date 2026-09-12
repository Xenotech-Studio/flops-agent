"""Database — session persistence protocol.

The framework recognizes only these granular operations; product code supplies
the backend, whether in-memory, SQLite, Redis plus SQLite, encrypted storage,
or hot/cold tiers. Separating append, truncate, and replacement lets a backend
load cold data lazily and avoids leaking backend-specific optimization hints
into the framework protocol.

Every method accepts ``keys`` for zero-knowledge deployments. They are passed
through without interpretation, caching, or persistence. Methods are
synchronous; a blocking implementation must use its own thread pool or async
adapter and must not perform network I/O on the event loop.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Protocol, runtime_checkable

from flops_agent.entities.session import Session


@runtime_checkable
class Database(Protocol):
    """Session persistence, addressed by ``session_id``."""

    # ── Reads ────────────────────────────────────────────────────────────────

    def load_meta(
        self, session_id: str, *, owner_id: str = "", keys: Optional[Mapping[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Session metadata, or ``None`` when absent.

        It is loaded separately from messages so callers need not fetch the
        full history merely to inspect metadata or existence.
        """
        ...

    def load_messages(
        self,
        session_id: str,
        *,
        owner_id: str = "",
        keys: Optional[Mapping[str, Any]] = None,
        start: int = 0,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Load a message range; ``limit=None`` means through the end.

        Range reads support hot/cold tiers, where old messages can be fetched
        from cold storage only when needed.
        """
        ...

    def count_messages(self, session_id: str, *, owner_id: str = "") -> int:
        """Message count, for pagination and tail reads without a full load."""
        ...

    # ── Writes ───────────────────────────────────────────────────────────────

    def append_messages(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        *,
        owner_id: str = "",
        keys: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Append messages; the common write path touches only the tail."""
        ...

    def truncate_messages(
        self, session_id: str, *, keep: int, owner_id: str = ""
    ) -> None:
        """Keep only the first ``keep`` messages, for regeneration."""
        ...

    def replace_message(
        self,
        session_id: str,
        index: int,
        message: Dict[str, Any],
        *,
        owner_id: str = "",
        keys: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Replace one message in place, for continuation or regeneration."""
        ...

    def patch_meta(
        self,
        session_id: str,
        fields: Dict[str, Any],
        *,
        owner_id: str = "",
        keys: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Partially update metadata without touching messages.

        A field value of ``None`` means delete that field, rather than storing
        a null value.
        """
        ...

    def create_session(
        self, session_id: str, *, owner_id: str = "", meta: Optional[Dict[str, Any]] = None
    ) -> None:
        """Create an empty session."""
        ...


class InMemoryDatabase:
    """Zero-configuration in-process reference implementation; not for production."""

    def __init__(self) -> None:
        self._meta: Dict[str, Dict[str, Any]] = {}
        self._messages: Dict[str, List[Dict[str, Any]]] = {}

    def load_meta(
        self, session_id: str, *, owner_id: str = "", keys: Optional[Mapping[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        meta = self._meta.get(session_id)
        return dict(meta) if meta is not None else None

    def load_messages(
        self,
        session_id: str,
        *,
        owner_id: str = "",
        keys: Optional[Mapping[str, Any]] = None,
        start: int = 0,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        msgs = self._messages.get(session_id, [])
        end = len(msgs) if limit is None else start + limit
        return [dict(m) for m in msgs[start:end]]

    def count_messages(self, session_id: str, *, owner_id: str = "") -> int:
        return len(self._messages.get(session_id, []))

    def append_messages(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        *,
        owner_id: str = "",
        keys: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._messages.setdefault(session_id, []).extend(dict(m) for m in messages)

    def truncate_messages(self, session_id: str, *, keep: int, owner_id: str = "") -> None:
        msgs = self._messages.get(session_id)
        if msgs is not None:
            del msgs[keep:]

    def replace_message(
        self,
        session_id: str,
        index: int,
        message: Dict[str, Any],
        *,
        owner_id: str = "",
        keys: Optional[Mapping[str, Any]] = None,
    ) -> None:
        msgs = self._messages.get(session_id)
        if msgs is not None and 0 <= index < len(msgs):
            msgs[index] = dict(message)

    def patch_meta(
        self,
        session_id: str,
        fields: Dict[str, Any],
        *,
        owner_id: str = "",
        keys: Optional[Mapping[str, Any]] = None,
    ) -> None:
        meta = self._meta.setdefault(session_id, {})
        for k, v in fields.items():
            if v is None:                # Protocol convention: None deletes the field.
                meta.pop(k, None)
            else:
                meta[k] = v

    def create_session(
        self, session_id: str, *, owner_id: str = "", meta: Optional[Dict[str, Any]] = None
    ) -> None:
        self._meta.setdefault(session_id, dict(meta or {}))
        self._messages.setdefault(session_id, [])


def sync_session(database: Database, session: Session, *, keys: Optional[Mapping[str, Any]] = None) -> int:
    """Synchronize an in-memory session to ``database``, writing only deltas.

    A longer history is appended, a shorter one is truncated, and an equal
    length rewrites the final message because it may have changed. This avoids
    a costly whole-history rewrite. Returns the appended count (negative for a
    truncation and zero for an equal-length tail replacement).
    """
    session_id, owner = session.session_id, session.owner_id
    stored = database.count_messages(session_id, owner_id=owner)
    current = len(session.messages)
    if current > stored:
        database.append_messages(
            session_id, session.messages[stored:], owner_id=owner, keys=keys
        )
        return current - stored
    if current < stored:
        database.truncate_messages(session_id, keep=current, owner_id=owner)
        return current - stored
    if current > 0:
        replace = getattr(database, "replace_message", None)
        if replace is not None:
            replace(
                session_id, current - 1, session.messages[-1],
                owner_id=owner, keys=keys,
            )
    return 0


__all__ = ["Database", "InMemoryDatabase", "sync_session"]
