"""RunStore — persistence protocol for run logs and terminal state.

Service-run supervision (background work, graceful shutdown, restart recovery,
and cross-process streaming) requires event logs and state to survive across
processes. Products choose the storage backend; the framework owns orchestration.
All methods are synchronous, and implementations should handle persistence
failures internally because persistence must not interrupt an active run.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Protocol, runtime_checkable

from flops_agent.entities.contracts import DispatchRecord


@runtime_checkable
class RunStore(Protocol):
    """Persistence boundary for run logs and terminal state."""

    def create_run(self, run_id: str, owner_id: str, session_id: str) -> None:
        """Register a new run with ``running`` status."""
        ...

    def append_chunks(self, run_id: str, parts: List[str]) -> None:
        """Append coalesced log segments; their indices are reconnect cursors."""
        ...

    def replace_chunk(self, run_id: str, index: int, part: str) -> bool:
        """Optionally replace an existing segment in place.

        This supports rolling snapshots. Returning ``False`` or raising rejects
        replacement, so callers fall back to append. Implementations must reject
        out-of-range indices rather than corrupting another position.
        """
        ...

    def mark_finished(self, run_id: str, status: str = "done") -> None:
        """Record a normal terminal state."""
        ...

    def mark_interrupted(self, run_id: str) -> None:
        """Record interruption by shutdown/reload, rather than user cancellation."""
        ...

    def request_stop(self, run_id: str) -> None:
        """Optionally persist a stop request across processes and restarts."""
        ...

    def stop_requested(self, run_id: str) -> bool:
        """Optionally report a persisted stop request for Runtime's watcher."""
        ...

    def list_active_run_ids(self) -> List[str]:
        """Optionally enumerate incomplete runs left by a prior process."""
        ...

    def mark_resuming(self, run_id: str) -> int:
        """Optionally transition to resuming and return the new resume count.

        Returning zero opens the circuit breaker and stops recovery.
        """
        ...

    def delete_run(self, run_id: str) -> None:
        """Optionally delete all persisted state for a run."""
        ...

    def get_meta(self, run_id: str) -> Optional["RunMeta"]:
        """Run metadata (owner, session, status, times), or ``None``."""
        ...

    def get_latest_run_id(self, owner_id: str, session_id: str) -> str:
        """ID of the session's most recent run, or an empty string."""
        ...

    def buffer_range(self, run_id: str, start: int) -> List[str]:
        """Read log segments from ``start`` for reconnect replay."""
        ...

    # ── Optional dispatch records for tool calls sent but not yet completed ───

    def record_dispatch(self, run_id: str, tool_call_id: str, record: Mapping[str, object]) -> None:
        """Optionally record or merge-update a dispatch record."""
        ...

    def pending_dispatches(self, run_id: str) -> Dict[str, DispatchRecord]:
        """Optionally return this run's outstanding dispatch records."""
        ...

    def clear_dispatches(self, run_id: str) -> None:
        """Optionally clear dispatch records once this step is complete."""
        ...


@dataclass
class RunMeta:
    """Return shape of ``InMemoryRunStore.get_meta``; products may customize it."""

    run_id: str
    owner_id: str
    session_id: str
    status: str
    created_at: float
    finished_at: Optional[float] = None
    resume_count: int = 0


class InMemoryRunStore:
    """Zero-configuration in-process ``RunStore`` reference implementation.

    It supports reconnect, interruption, and recovery semantics for examples,
    tests, and small single-process tools; production needs shared storage.
    """

    def __init__(self) -> None:
        self._buffers: Dict[str, List[str]] = {}
        self._meta: Dict[str, RunMeta] = {}
        self._stop: set[str] = set()
        self._latest: Dict[tuple[str, str], str] = {}
        self._dispatches: Dict[str, Dict[str, DispatchRecord]] = {}

    # Log segments and terminal state
    def create_run(self, run_id: str, owner_id: str, session_id: str) -> None:
        self._meta[run_id] = RunMeta(run_id, owner_id, session_id, "running", time.time())
        self._buffers.setdefault(run_id, [])
        self._latest[(owner_id, session_id)] = run_id

    def get_latest_run_id(self, owner_id: str, session_id: str) -> str:
        return self._latest.get((owner_id, session_id), "")

    def append_chunks(self, run_id: str, parts: List[str]) -> None:
        self._buffers.setdefault(run_id, []).extend(parts)

    def buffer_range(self, run_id: str, start: int) -> List[str]:
        return self._buffers.get(run_id, [])[max(0, start):]

    def buffer_len(self, run_id: str) -> int:
        return len(self._buffers.get(run_id, []))

    def replace_chunk(self, run_id: str, index: int, part: str) -> bool:
        """Replace segment ``index`` in place, rejecting out-of-range indices."""
        buf = self._buffers.get(run_id)
        if buf is None or index < 0 or index >= len(buf):
            return False
        buf[index] = part
        return True

    def get_meta(self, run_id: str) -> Optional[RunMeta]:
        return self._meta.get(run_id)

    def mark_finished(self, run_id: str, status: str = "done") -> None:
        m = self._meta.get(run_id)
        if m is not None:
            m.status = status
            m.finished_at = time.time()

    def mark_interrupted(self, run_id: str) -> None:
        m = self._meta.get(run_id)
        if m is not None:
            m.status = "interrupted"

    # Stop requests (in-process only for this implementation)
    def request_stop(self, run_id: str) -> None:
        self._stop.add(run_id)

    def stop_requested(self, run_id: str) -> bool:
        return run_id in self._stop

    # Recovery
    def list_active_run_ids(self) -> List[str]:
        return [rid for rid, m in self._meta.items() if m.status in ("running", "interrupted")]

    def mark_resuming(self, run_id: str) -> int:
        m = self._meta.get(run_id)
        if m is None:
            return 0
        m.status = "running"
        m.resume_count += 1
        return m.resume_count

    def delete_run(self, run_id: str) -> None:
        self._meta.pop(run_id, None)
        self._buffers.pop(run_id, None)
        self._dispatches.pop(run_id, None)

    # Dispatch records
    def record_dispatch(self, run_id: str, tool_call_id: str, record: Mapping[str, object]) -> None:
        self._dispatches.setdefault(run_id, {}).setdefault(tool_call_id, {}).update(record)

    def pending_dispatches(self, run_id: str) -> Dict[str, DispatchRecord]:
        return {k: dict(v) for k, v in self._dispatches.get(run_id, {}).items()}

    def clear_dispatches(self, run_id: str) -> None:
        self._dispatches.pop(run_id, None)


__all__ = ["RunStore", "RunMeta", "InMemoryRunStore"]
