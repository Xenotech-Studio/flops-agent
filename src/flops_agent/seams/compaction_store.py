"""CompactionStore — persistence protocol for compaction records.

It sits alongside :mod:`flops_agent.seams.database` rather than inside it:
compaction records are a distinct, sparse entity with an archive/active/
replacement lifecycle, not a third kind of session field.  Methods are
synchronous and address records by ``session_id``; ``owner_id`` and ``keys``
are passed through without interpretation.  Generation and trigger policy
lives in :mod:`flops_agent.engine.compaction`; this module only stores the
archive and its active record.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@dataclass
class CompactionRecord:
    """A record summarizing ``messages[0:covers_exclusive_end)`` as ``summary_text``.

    Attributes:
        id: Record ID; any unique string is accepted.
        covers_exclusive_end: Exclusive upper endpoint, matching Python slices.
        summary_text: Summary body.
        created_at: Creation time; the product chooses its representation.
        supersedes: ID of the replaced record, for audit trails.
    """

    id: str
    covers_exclusive_end: int
    summary_text: str
    created_at: Any = None
    supersedes: Optional[str] = None


@runtime_checkable
class CompactionStore(Protocol):
    """Read/write protocol for compaction records, addressed by ``session_id``."""

    def list_compactions(
        self, session_id: str, *, owner_id: str = ""
    ) -> List[CompactionRecord]:
        """All archived records, including superseded ones, in unspecified order."""
        ...

    def get_active(
        self, session_id: str, *, owner_id: str = ""
    ) -> Optional[CompactionRecord]:
        """The current active record, or ``None``."""
        ...

    def append_and_activate(
        self,
        session_id: str,
        record: CompactionRecord,
        *,
        prune_above_exclusive_end: Optional[int] = None,
        owner_id: str = "",
    ) -> bool:
        """Persist a record and immediately make it active.

        ``prune_above_exclusive_end`` also removes archived records with a
        larger coverage endpoint. ``None`` disables pruning. Return whether a
        record was actually written.
        """
        ...

    def set_active(
        self, session_id: str, record_id: Optional[str], *, owner_id: str = ""
    ) -> None:
        """Point active at another archived record; ``None`` clears it."""
        ...

    def prune_above(
        self, session_id: str, cap_exclusive_end: int, *, owner_id: str = ""
    ) -> bool:
        """Delete archived records above the cap and report whether anything changed."""
        ...


class InMemoryCompactionStore:
    """Zero-configuration in-process reference implementation; not for production."""

    def __init__(self) -> None:
        self._archive: Dict[str, Dict[str, CompactionRecord]] = {}
        self._active: Dict[str, Optional[str]] = {}

    def list_compactions(
        self, session_id: str, *, owner_id: str = ""
    ) -> List[CompactionRecord]:
        return list(self._archive.get(session_id, {}).values())

    def get_active(
        self, session_id: str, *, owner_id: str = ""
    ) -> Optional[CompactionRecord]:
        rid = self._active.get(session_id)
        if rid is None:
            return None
        return self._archive.get(session_id, {}).get(rid)

    def append_and_activate(
        self,
        session_id: str,
        record: CompactionRecord,
        *,
        prune_above_exclusive_end: Optional[int] = None,
        owner_id: str = "",
    ) -> bool:
        bucket = self._archive.setdefault(session_id, {})
        bucket[record.id] = record
        self._active[session_id] = record.id
        if prune_above_exclusive_end is not None:
            self.prune_above(session_id, prune_above_exclusive_end, owner_id=owner_id)
        return True

    def set_active(
        self, session_id: str, record_id: Optional[str], *, owner_id: str = ""
    ) -> None:
        self._active[session_id] = record_id

    def prune_above(
        self, session_id: str, cap_exclusive_end: int, *, owner_id: str = ""
    ) -> bool:
        bucket = self._archive.get(session_id)
        if not bucket:
            return False
        drop = [rid for rid, r in bucket.items() if r.covers_exclusive_end > cap_exclusive_end]
        for rid in drop:
            bucket.pop(rid, None)
        return bool(drop)


__all__ = ["CompactionRecord", "CompactionStore", "InMemoryCompactionStore"]
