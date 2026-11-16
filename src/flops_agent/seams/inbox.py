"""Inbox — delivery of input that arrives outside the current turn.

An agent may be working when a user or external system sends another message.
The framework supports two delivery boundaries: ``deliver="turn"`` queues it
until the current tool loop and final response finish, while ``deliver="step"``
inserts it at the next loop boundary, or restarts an in-flight model call
that has not produced visible text or a tool call yet. Reasoning alone does
not close this early-steer window. At a turn
boundary, ``poll("turn")`` returns both kinds so a step message never remains
queued after a run ends.

The framework owns polling, history updates, and continuation; products choose
queue storage, message shape, and cross-process visibility. Delivery appends
the original message to a complete valid history and never fabricates an empty
assistant turn.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Dict, List, Protocol, runtime_checkable


@runtime_checkable
class Inbox(Protocol):
    """Delivery-queue protocol; implementations choose storage and visibility."""

    async def poll(self, session: Any, boundary: str) -> List[Dict[str, Any]]:
        """Remove and return messages deliverable at this boundary, in queue order.

        Args:
            session: Current session, used to locate its queue.
            boundary: ``"step"`` after a tool step, or ``"turn"`` when the
                model no longer needs tools. The turn boundary returns both.

        Returns:
            Message dictionaries (normally ``{"role": "user", "content": ...}``),
            or an empty list.
        """
        ...


@runtime_checkable
class SteerableInbox(Inbox, Protocol):
    """Optional wakeup contract, without transferring ownership of queued input.

    ``wait_for_steer`` must be cancellable, check already pending input, and
    return only when ``poll(session, "steer")`` can supply eligible messages.
    ``steer`` excludes inputs meant for a completed turn or tool boundary.
    A notification is only a hint: poll/admission remains authoritative.
    """

    async def wait_for_steer(self, session: Any) -> None: ...


class MemoryInbox:
    """Zero-configuration default: an in-process queue.

    It works out of the box for single-process scripts, tests, and small services::

        runtime.deliver(session_id, {"role": "user", "content": "One addition…"})

    Multi-worker or multi-process deployments need a shared ``Inbox`` implementation.
    """

    def __init__(self) -> None:
        self._queues: Dict[str, Dict[str, List[Dict[str, Any]]]] = defaultdict(
            lambda: {"step": [], "turn": []}
        )

        self._waiters: Dict[str, asyncio.Event] = {}

    def push(self, session_id: str, message: Dict[str, Any], *, deliver: str = "turn") -> None:
        """Queue one message: ``"turn"`` waits for completion; ``"step"`` uses the next boundary."""
        if deliver not in ("turn", "step"):
            raise ValueError(f'deliver must be "turn" or "step", got {deliver!r}')
        key = str(session_id)
        self._queues[key][deliver].append(dict(message))
        if deliver == "step" and key in self._waiters:
            self._waiters[key].set()

    async def wait_for_steer(self, session: Any) -> None:
        key = str(getattr(session, "session_id", session))
        ready = asyncio.Event()
        self._waiters[key] = ready
        try:
            if not self._queues[key]["step"]:
                await ready.wait()
        finally:
            if self._waiters.get(key) is ready:
                self._waiters.pop(key, None)

    async def poll(self, session: Any, boundary: str) -> List[Dict[str, Any]]:
        q = self._queues.get(str(getattr(session, "session_id", session)))
        if not q:
            return []
        out: List[Dict[str, Any]] = []
        # Step messages are deliverable at either boundary; turn messages only at completion.
        out.extend(q["step"])
        q["step"] = []
        if boundary == "turn":
            out.extend(q["turn"])
            q["turn"] = []
        return out


__all__ = ["Inbox", "SteerableInbox", "MemoryInbox"]
