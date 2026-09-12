"""Server-side dispatch hub for remote executor tool calls.

``dispatch`` registers a waiter, sends a task when appropriate, relays stream
updates, and returns a terminal result. ``receive`` routes executor messages,
deduplicates terminal outcomes, parks outcomes that arrive before a resumed
run registers its waiter, and returns acknowledgements. The ledger is durable;
the in-process waiter queues deliberately are not.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional, Protocol, runtime_checkable

from . import protocol as P

logger = logging.getLogger(__name__)

SendFn = Callable[[Dict[str, Any]], Awaitable[Any]]
StreamSink = Callable[[Dict[str, Any]], None]
Translate = Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]


@runtime_checkable
class DispatchLedger(Protocol):
    """Durable tables for seen terminal task IDs and parked outcomes.

    Implementations should handle failures internally: the ledger must not
    interrupt dispatch.
    """

    def seen(self, task_id: str) -> bool:
        """Whether this task's terminal outcome has already been received."""
        ...

    def mark_seen(self, task_id: str) -> None:
        ...

    def park(self, task_id: str, outcome: Dict[str, Any]) -> None:
        """Park a terminal outcome when no waiter is registered."""
        ...

    def take_parked(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Take and delete a parked outcome, or return ``None``."""
        ...


class InMemoryDispatchLedger:
    """In-process reference implementation for tests and single-host use."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._parked: Dict[str, Dict[str, Any]] = {}

    def seen(self, task_id: str) -> bool:
        return task_id in self._seen

    def mark_seen(self, task_id: str) -> None:
        self._seen.add(task_id)

    def park(self, task_id: str, outcome: Dict[str, Any]) -> None:
        self._parked[task_id] = dict(outcome)

    def take_parked(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self._parked.pop(task_id, None)


def default_translate(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Translate executor stream messages for ``stream_sink``, or return ``None``."""
    mtype = message.get("type")
    if mtype == P.STREAM_META:
        fields = message.get("set")
        if isinstance(fields, dict) and fields:
            return {"set": fields}
        return None
    if mtype == P.STREAM_CHUNK:
        delta: Dict[str, Any] = {}
        chunk = message.get("chunk")
        if chunk is not None and str(chunk):
            delta["stdout_append"] = str(chunk)
        if message.get("patches") is not None:
            delta["patches"] = message["patches"]
        return delta or None
    return None


class ExecutorLink:
    """Dispatch hub shared by one server process and its transport/router."""

    def __init__(self, *, ledger: Optional[DispatchLedger] = None) -> None:
        self.ledger: DispatchLedger = ledger if ledger is not None else InMemoryDispatchLedger()
        self._waiting: Dict[str, "asyncio.Queue[Dict[str, Any]]"] = {}

    # ── Transport entrypoint ─────────────────────────────────────────────────

    def handles(self, message_type: Any) -> bool:
        """Whether a message type belongs to the dispatch protocol."""
        return message_type in P.INBOUND_TYPES

    def receive(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Receive one executor message and return an acknowledgement if needed.

        Non-protocol messages and messages without ``task_id`` are ignored.
        """
        mtype = message.get("type")
        if not self.handles(mtype):
            return None
        task_id = P.task_id_of(message)
        if not task_id:
            return None
        if mtype in P.TERMINAL_TYPES:
            if not self._seen(task_id):
                self._mark_seen(task_id)
                queue = self._waiting.get(task_id)
                if queue is not None:
                    queue.put_nowait(message)
                else:
                    # No waiter yet: park it for the resumed dispatch.
                    self._park(task_id, P.outcome_of(message))
            # Duplicate terminal outcomes still need acknowledgement.
            return P.result_ack(task_id)
        queue = self._waiting.get(task_id)
        if queue is not None:
            queue.put_nowait(message)
        return None

    def waiting(self, task_id: str) -> bool:
        """Whether an in-process waiter exists for this task."""
        return task_id in self._waiting

    # ── Dispatch entrypoint ──────────────────────────────────────────────────

    async def dispatch(
        self,
        *,
        task_id: str,
        message: Dict[str, Any],
        send: Optional[SendFn],
        stream_sink: Optional[StreamSink] = None,
        timeout: float,
        resume: bool = False,
        translate: Optional[Translate] = None,
    ) -> Any:
        """Dispatch once and wait for its terminal outcome.

        A resumed dispatch first takes a parked outcome or waits for replay;
        it never sends ``run_tool`` again, avoiding duplicate execution.
        """
        if resume:
            parked = self._take_parked(task_id)
            if parked is not None:
                logger.info("dispatch resume: drained parked %s for task=%s", parked.get("type"), task_id)
                return P.outcome_to_tool_result(parked)
        elif send is None:
            raise ValueError("dispatch without a transport: send is required unless resume=True")
        queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()
        self._waiting[task_id] = queue
        to_delta = translate or default_translate
        try:
            if not resume:
                await send(message)  # pyright: ignore[reportOptionalCall] — guarded above
            else:
                logger.info("dispatch resume: waiting for replay of task=%s (run_tool not re-sent)", task_id)
            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout
            while True:
                remaining = max(0.01, deadline - loop.time())
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    return {"success": False, "error": f"Timeout ({timeout}s)"}
                mtype = msg.get("type")
                if mtype in P.TERMINAL_TYPES:
                    return P.outcome_to_tool_result(P.outcome_of(msg))
                if stream_sink is not None:
                    delta = to_delta(msg)
                    if delta:
                        stream_sink(delta)
        finally:
            self._waiting.pop(task_id, None)

    # ── Ledger fallbacks: failures must not interrupt dispatch ────────────────

    def _seen(self, task_id: str) -> bool:
        try:
            return bool(self.ledger.seen(task_id))
        except Exception:
            logger.warning("dispatch ledger seen() failed task=%s (treated as unseen)", task_id, exc_info=True)
            return False

    def _mark_seen(self, task_id: str) -> None:
        try:
            self.ledger.mark_seen(task_id)
        except Exception:
            logger.warning("dispatch ledger mark_seen() failed task=%s", task_id, exc_info=True)

    def _park(self, task_id: str, outcome: Dict[str, Any]) -> None:
        try:
            self.ledger.park(task_id, outcome)
        except Exception:
            logger.warning("dispatch ledger park() failed task=%s (outcome lost)", task_id, exc_info=True)

    def _take_parked(self, task_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self.ledger.take_parked(task_id)
        except Exception:
            logger.warning("dispatch ledger take_parked() failed task=%s", task_id, exc_info=True)
            return None


__all__ = ["DispatchLedger", "InMemoryDispatchLedger", "ExecutorLink", "default_translate"]
