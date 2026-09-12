"""Run — one round of execution.

**It is not "a stream to be consumed" — it's "a process already underway."**
That distinction shapes the whole design:

* Execution advances in the background on its own, **it does not stop just
  because nobody is listening** — the client can close the tab, and background
  tasks and long-running tool calls keep going regardless.
* Subscribers come and go freely, **there can be several at once** (multiple
  devices, refreshes, reconnects), each attaching from wherever they left off.
* Hence "iterating a run" is not "driving a run" — it's "subscribing to a run".

Structurally there are two views, stitched together by a cursor:

===========  ==============================  ===================  =========================
View         Content                          For whom             Why
===========  ==============================  ===================  =========================
live tap     raw, per-event stream            currently connected  the typewriter effect
                                               subscribers          needs per-token granularity
log          merged segments (decided by      reconnects / new     replaying five thousand
             the Coalescer)                   subscribers          single-token events wastes
                                               backfilling history  both network and rendering
===========  ==============================  ===================  =========================

Merging happens **at write time**: every event is fed to both the coalescer
(accumulated into segments for the log) and to currently connected subscribers.
The coalescer is **stateful** — it keeps a "still accumulating" pending segment
and only emits a finished segment once it can't keep accumulating, so the run
must call ``flush()`` when it ends, or the log will be missing its tail.

.. warning::
   **The cursor must be handed out by the server; the client must never count
   its own.** The log's index space is decided by the coalescer and does not
   map 1:1 to the raw event count — subscribers receive raw events while the
   log stores merged segments, so a subscriber has no way to derive its
   position in the log from "how many events have I received". Reconnecting
   after a disconnect must send back :attr:`Delivery.cursor`. Since this rule
   is easy to miss if it isn't baked into the contract, the cursor is attached
   to **every single delivery's return value**, so it's hard to forget.

(The module is named ``execution`` rather than ``run``: ``flops_agent.run`` is
currently occupied by a legacy ChatV2-specific package — that's product code
living inside the framework; this module can be renamed once that's moved back
to the product layer. Only the class names are exported publicly, so users
never hit the module path.)
"""
from __future__ import annotations

from typing_extensions import override
import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol, Set, Tuple, runtime_checkable

from flops_agent.seams.run_store import RunStore

logger = logging.getLogger(__name__)


class RunStatus(str, Enum):
    """The status of one round of execution."""

    RUNNING = "running"
    DONE = "done"
    """Completed normally."""
    STOPPED = "stopped"
    """Interrupted via :meth:`Run.stop`."""
    FAILED = "failed"
    """An exception was raised during execution."""
    SUSPENDED = "suspended"
    """Paused waiting on a user response (confirmation card / multiple choice),
    to be resumed by a later request."""


@dataclass(frozen=True)
class Delivery:
    """One delivery: an event plus the cursor value at the time it was received.

    ``cursor`` is **the credential for reconnecting after a disconnect** — the
    next ``subscribe(from_cursor=cursor)`` call picks up right where you left
    off, with no gaps and no duplicates. It is not "the Nth event" — see the
    warning in the module docstring.
    """

    event: Any
    cursor: int
    replayed: bool = False
    """True = backfilled history (from the log, already merged); False = live
    (raw granularity)."""


@dataclass(frozen=True)
class LogWrite:
    """A segment emitted by the coalescer, plus intent for "how to write it
    into the log".

    The coalescer can emit a bare event directly (equivalent to
    ``LogWrite(event)``, i.e. append), or it can emit this type to express
    **an in-place overwrite**:

    ``replaces_open_slot=True`` — this segment is "a new version of the same
    rolling snapshot". The first time it's emitted it's appended as usual and
    that position is recorded as **an open slot**; every subsequent emission
    overwrites that same position, so the log's length never grows.

    Why this is needed: while a long-running tool is in flight (an environment
    setup command, a subagent), the coalescer periodically writes "the
    cumulative state as of right now" into the log so a mid-run reconnecter
    can backfill history. These snapshots have ``set``-the-whole-key overwrite
    semantics — **each one fully subsumes the previous one** — so appending
    each one separately would let a 40-minute command pile up thousands of
    entries, each carrying the full cumulative output; both storage and replay
    would be O(n²) (measured: a 9-minute run already had 30x redundancy).
    Overwriting in place collapses that back down to O(1) per window.

    **All index arithmetic is owned entirely by** :class:`Run`; the
    coalescer's only job is to say "this is the same rolling snapshot". That
    way resume (log pre-warmed from the store, starting at a nonzero offset)
    never requires the coalescer to know any absolute position.
    """

    event: Any
    replaces_open_slot: bool = False


def _as_log_write(part: Any) -> LogWrite:
    """Bare event -> a LogWrite with append semantics. Lets coalescers that
    don't know about this type (e.g. PassthroughCoalescer and third-party
    implementations) keep working unmodified."""
    return part if isinstance(part, LogWrite) else LogWrite(part)


@runtime_checkable
class Coalescer(Protocol):
    """Live events -> log segments. **The mechanism lives in the framework;
    the policy is decided by the product layer.**

    Which events can be merged, and how, is a contract locked to the
    frontend's rendering logic — that's product knowledge. The framework is
    only responsible for the mechanics of "merge while writing, flush at the
    end". The default implementation is the identity function (log and live
    share the same granularity, so the cursor equals the event sequence
    number); third parties don't need to know this concept exists.

    Whatever ``feed`` / ``flush`` emit can be either a bare event (append) or
    a :class:`LogWrite` (can express an in-place overwrite). Implementations
    that only ever emit bare events are completely unaffected.
    """

    def feed(self, event: Any) -> List[Any]:
        """Consume one event, emit the segment(s) that are **already
        complete** (may be empty — still accumulating)."""
        ...

    def flush(self) -> List[Any]:
        """End of run: emit whatever segment is still being accumulated. If
        this isn't called, the log will be missing its tail."""
        ...


class PassthroughCoalescer:
    """Default coalescer: no merging. Log and live share the same
    granularity; the cursor is just the event sequence number."""

    def feed(self, event: Any) -> List[Any]:
        return [event]

    def flush(self) -> List[Any]:
        return []


class Run:
    """A handle to one round of execution.

    Once a caller has it, they can: iterate it (= subscribe), resubscribe from
    a cursor, interrupt it, or check its status.
    **The Runner drives it forward — not whoever is iterating it.**
    """

    #: The background task driving this run (attached by ``Runtime.start``);
    #: cancelling it, shutting it down, or waiting for it to finish all go
    #: through this handle.
    task: Optional["asyncio.Task[None]"] = None

    def __init__(
        self,
        run_id: str,
        *,
        session_id: str = "",
        owner_id: str = "",
        coalescer: Optional[Coalescer] = None,
        store: Optional[RunStore] = None,
    ):
        self.id = run_id
        self.session_id = session_id
        self.owner_id = owner_id
        self._coalescer: Coalescer = coalescer or PassthroughCoalescer()
        self._log: List[Any] = []
        self._subscribers: Set["asyncio.Queue[Optional[Delivery]]"] = set()
        self._status = RunStatus.RUNNING
        self._error: Optional[BaseException] = None
        self._stop_requested = False
        self._stop_listeners: List[Any] = []
        self._lock = asyncio.Lock()
        self._finished = asyncio.Event()
        self.created_at = time.time()
        self.finished_at: Optional[float] = None
        #: Interrupted by a shutdown/reload (not a user cancel). Set by
        #: ``Runtime.shutdown``; ``finish`` uses this to record `interrupted`
        #: rather than `done` in the store, and the product layer's recovery
        #: flow uses it to find runs that need to be resumed.
        self.interrupted = False
        #: Absolute index in the log of the currently open "rolling snapshot"
        #: slot; None = no open slot. See :class:`LogWrite`.
        self._open_slot: Optional[int] = None
        #: Whether in-place overwrite is available. The log's and the
        #: store's indices must stay in exact lockstep for LSET to work —
        #: the moment they're found to be misaligned this permanently
        #: downgrades to append-only (behavior reverts to how it was before
        #: the slot mechanism existed — worst case is just the status quo).
        #: Corrupting someone else's position is irreversible, so we'd
        #: rather degrade than risk it.
        self._inplace_ok = True
        # RunStore (optional): keeps log segments and the final status alive
        # across process restarts. Registered at construction time; the store
        # implementation is expected to swallow its own exceptions —
        # persistence is a best-effort side channel and must never blow up
        # the run (we catch here too, as a backstop in case an
        # implementation forgets to).
        self._store = store
        if store is not None:
            try:
                store.create_run(run_id, owner_id, session_id)
            except Exception:
                logger.exception("run store create_run failed run=%s", run_id)
            # Capability probing happens **before touching the log**: if the
            # store doesn't support overwrite, disable it once up front so
            # ``_apply_to_log`` sticks to append-only for the whole run.
            # Otherwise you'd get a length divergence where "the log was
            # overwritten but the store could only append" — and length IS
            # the cursor, so divergence means misalignment.
            if getattr(store, "replace_chunk", None) is None:
                self._inplace_ok = False
            self._seed_log_from_store()

    def _seed_log_from_store(self) -> None:
        """When resuming a run (reusing an old run_id), pre-warm ``_log`` with
        whatever log segments already exist in the store.

        Two reasons, both necessary:

        1. **Correctness (fixes a real bug)**: ``_log`` always starts empty,
           but the store still holds the N segments written by the previous
           process. Without this, a subscriber that connects after resume
           would replay from ``_log`` and only ever see what happened after
           the reload — everything from before the reload vanishes without a
           trace.
        2. **A precondition for in-place overwrite**: the slot index is
           shared between ``_log`` and the store's LIST, so if the two start
           at different offsets, overwrites land in the wrong place.

        A no-op for a brand-new run (empty buffer). If the store doesn't
        implement the read interface, or the read fails, this conservatively
        skips seeding and disables in-place overwrite — if the two can't be
        kept aligned, don't LSET.
        """
        store = self._store
        reader = getattr(store, "buffer_range", None)
        sizer = getattr(store, "buffer_len", None)
        if reader is None or sizer is None:
            return
        try:
            n = int(sizer(self.id) or 0)
            if n <= 0:
                return
            seeded = list(reader(self.id, 0) or [])
        except Exception:
            logger.exception("run store seed log failed run=%s", self.id)
            self._inplace_ok = False
            return
        if len(seeded) != n:
            # Store was mutated concurrently while we were reading it
            # partway through: can't trust it, fall back to an empty log
            # and disable in-place overwrite.
            logger.warning(
                "run %s seed log size mismatch (llen=%d read=%d), in-place disabled",
                self.id, n, len(seeded),
            )
            self._inplace_ok = False
            return
        self._log = seeded
        logger.info("run %s resumed with %d preloaded log parts", self.id, len(seeded))

    # ── status ───────────────────────────────────────────────────────────────

    @property
    def status(self) -> RunStatus:
        return self._status

    @property
    def done(self) -> bool:
        return self._status is not RunStatus.RUNNING

    @property
    def error(self) -> Optional[BaseException]:
        """The exception raised, when status is ``FAILED``."""
        return self._error

    @property
    def cursor(self) -> int:
        """Current log length — subscribing from here means "just give me
        what's new"."""
        return len(self._log)

    @property
    def stop_requested(self) -> bool:
        """Whether an interrupt has been requested. The executor checks this
        at its checkpoints to decide when to wind down."""
        return self._stop_requested

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # ── write side (called by the Runner) ───────────────────────────────────

    async def emit(self, event: Any) -> None:
        """Emit one event: feed it to the coalescer to accumulate into the
        log (persisted synchronously if a store is attached), and deliver it
        as-is to currently connected subscribers."""
        parts = self._coalescer.feed(event)
        async with self._lock:
            appended, replaced = self._apply_to_log(parts)
            cursor = self._live_cursor()
            targets = list(self._subscribers)
        self._persist_parts(appended, replaced)
        delivery = Delivery(event=event, cursor=cursor, replayed=False)
        for q in targets:
            try:
                q.put_nowait(delivery)
            except Exception:
                # Subscriber queue full / abandoned: fine to drop this one,
                # it can be backfilled from the log on reconnect
                pass

    # ── writing the log: append vs. in-place overwrite of an open slot ─────

    def _apply_to_log(self, parts: List[Any]) -> Tuple[List[Any], List[Tuple[int, Any]]]:
        """Write the segments emitted by the coalescer into ``_log``,
        returning ``(appended, replaced)``.

        ``appended`` is the list of segments to RPUSH; ``replaced`` is
        ``[(absolute_index, segment)]`` to LSET.
        **Must be called while holding** ``self._lock`` — the slot index and
        the log length are a single fact that must hold together atomically.
        """
        appended: List[Any] = []
        replaced: List[Tuple[int, Any]] = []
        for raw in parts or []:
            w = _as_log_write(raw)
            if w.replaces_open_slot and self._inplace_ok and self._open_slot is not None:
                self._log[self._open_slot] = w.event
                replaced.append((self._open_slot, w.event))
                continue
            self._log.append(w.event)
            appended.append(w.event)
            # The first segment of a rolling snapshot: reserve a slot and
            # record it as open; later versions overwrite it in place. A
            # non-snapshot segment (a regular event) landing means the
            # previous window has ended — close the slot.
            self._open_slot = (len(self._log) - 1) if (w.replaces_open_slot and self._inplace_ok) else None
        return appended, replaced

    def _live_cursor(self) -> int:
        """The reconnect cursor to attach to a live delivery.

        Normally this is ``len(_log)`` (a subscriber has already seen
        everything live, so reconnecting only needs what's new). But **while
        a slot is open it must point at the slot itself** — what's in the
        slot has ``set``-the-whole-key overwrite semantics for the cumulative
        state, so a reconnecting subscriber that replays from it gets caught
        up on the entire disconnect window in one frame. Reporting
        ``len(_log)`` instead would skip the slot and permanently lose
        whatever happened during the disconnect (visible as a chunk missing
        from the middle of a tool's output). One extra redundant frame buys
        self-healing.
        """
        if self._open_slot is not None:
            return self._open_slot
        return len(self._log)

    def _persist_parts(
        self, appended: List[Any], replaced: Optional[List[Tuple[int, Any]]] = None
    ) -> None:
        if self._store is None:
            return
        if replaced:
            replacer = getattr(self._store, "replace_chunk", None)
            for idx, ev in replaced:
                try:
                    if replacer is None:
                        raise RuntimeError("store has no replace_chunk")
                    if not replacer(self.id, idx, ev):
                        raise RuntimeError("replace_chunk rejected")
                except Exception as exc:
                    # **Do not fall back to append**: the log has already
                    # overwritten that position, so appending to the store
                    # too would make the two diverge in length — and length
                    # IS the cursor. So we just disable in-place overwrite
                    # going forward; the store keeps the previous snapshot in
                    # that slot for now. The final state for this window will
                    # land as an appended entry right after it, and since
                    # snapshots use set-the-whole-key overwrite semantics,
                    # replaying up through that later entry self-heals.
                    # Better briefly stale than misaligned.
                    self._inplace_ok = False
                    logger.warning(
                        "run store replace_chunk failed run=%s idx=%d (%s); "
                        "in-place disabled for this run, slot keeps previous snapshot",
                        self.id, idx, exc,
                    )
        if not appended:
            return
        try:
            self._store.append_chunks(self.id, appended)
        except Exception:
            logger.exception("run store append_chunks failed run=%s", self.id)

    async def finish(
        self,
        status: RunStatus = RunStatus.DONE,
        *,
        error: Optional[BaseException] = None,
        error_message: Optional[str] = None,
        emit_error: bool = True,
    ) -> None:
        """Wind down: flush whatever the coalescer is still holding, set the
        final status, wake up every subscriber. Idempotent.
        ``error_message`` is the user-facing text (produced by
        ``Runner.describe_error``); if not given, falls back to the raw
        exception text.
        ``emit_error=False``: the caller has already emitted an Error event
        itself via ``Runner.emit`` (the product layer's serialization
        boundary) — this just records the final status without emitting
        again; that's the path ``Runner.drive`` takes. Product-layer code
        that calls finish directly should use the default.

        On failure, **emit an Error event before the sentinel**. Without it,
        subscribers just see the stream end normally — they have no way to
        tell "it finished" from "it crashed", and the user sees a reply that
        trails off with no error shown at all. Recording the final status on
        the Run object alone isn't enough: a subscriber that reconnects after
        a disconnect only gets the log, and if this event isn't in the log
        they'll never see it.
        """
        if emit_error and status is RunStatus.FAILED and error is not None and not self.done:
            from flops_agent.entities.events import Error as _ErrorEvent

            await self.emit(_ErrorEvent(message=error_message or str(error) or type(error).__name__, exc=error))
        flush_parts = self._coalescer.flush()
        async with self._lock:
            if self.done:
                return
            appended, replaced = self._apply_to_log(flush_parts)
            buf_len = len(self._log)
            self._status = status
            self._error = error
            targets = list(self._subscribers)
            self._subscribers.clear()
        self.finished_at = time.time()
        self._persist_parts(appended, replaced)
        if self._store is not None:
            try:
                if self.interrupted:
                    self._store.mark_interrupted(self.id)
                else:
                    # Record the final status in the store as-is
                    # (done/stopped/failed/suspended); a suspended run has
                    # not "finished" in the `done` sense — if a suspended run
                    # were recorded in the store as a normal completion,
                    # anything checking session liveness or querying status
                    # would be misled.
                    self._store.mark_finished(self.id, status=str(status.value))
            except Exception:
                logger.exception("run store mark_finished/interrupted failed run=%s", self.id)
        # Pre-sentinel delivery seam: lets the product layer hand connected
        # subscribers one last frame before the sentinel (e.g. a "final
        # reconnect cursor" — subscribers already saw the flushed segment
        # live and just need the new cursor; frames like this **don't go
        # into the log**, since putting them there would give reconnecting
        # subscribers a stale cursor).
        for extra in self._pre_sentinel_deliveries(flush_parts, buf_len):
            for q in targets:
                try:
                    q.put_nowait(extra)
                except Exception:
                    pass
        for q in targets:
            try:
                q.put_nowait(None)     # sentinel: subscribers use this to end their iteration
            except Exception:
                pass
        self._finished.set()

    def _pre_sentinel_deliveries(self, flush_parts: List[Any], buf_len: int) -> List["Delivery"]:
        """Deliveries to hand connected subscribers just before the sentinel
        during finish's wind-down (not written to the log). Empty by
        default."""
        return []

    async def interrupt_for_shutdown(self) -> None:
        """Interrupt for shutdown: mark interrupted, record it in the store,
        wake up every subscriber — **without setting a final status**.

        The difference from :meth:`finish`: at shutdown time the driving task
        is still alive (it gets cancelled afterward by the product layer /
        event loop), so the run must not be marked done — the recovery flow
        relies on `interrupted` in the store to find it and resume it later.
        Subscribers receive the sentinel and close their stream immediately
        (which is what triggers the frontend to reconnect), matching the
        semantics of the legacy product's cleanup hook.
        """
        async with self._lock:
            if self.done:
                return
            self.interrupted = True
            targets = list(self._subscribers)
        if self._store is not None:
            try:
                self._store.mark_interrupted(self.id)
            except Exception:
                logger.exception("run store mark_interrupted failed run=%s", self.id)
        for q in targets:
            try:
                q.put_nowait(None)
            except Exception:
                pass

    # ── read side (subscribers) ─────────────────────────────────────────────

    async def subscribe(self, from_cursor: int = 0) -> AsyncIterator[Delivery]:
        """Subscribe: first backfill ``log[from_cursor:]``, then continue with
        live events.

        Args:
            from_cursor: starting cursor. ``0`` = backfill everything from
                the start (the normal case for a new client); on reconnect,
                pass back the cursor from the last :attr:`Delivery.cursor`
                you received.

        Backfilled history and live events are **at different granularities**
        (the former is merged segments, the latter is raw events) — this is
        deliberate: history should be compact, live should be fine-grained.
        Callers shouldn't assume the two line up 1:1 or try to dedupe across
        them.
        """
        idx = max(0, int(from_cursor or 0))
        async with self._lock:
            backlog = list(self._log[idx:])
            q: Optional["asyncio.Queue[Optional[Delivery]]"] = None
            if not self.done:
                q = asyncio.Queue()
                self._subscribers.add(q)
        # Deliver the backlog outside the lock: while yielding through a
        # large log we shouldn't hold the lock and block emit
        for i, part in enumerate(backlog, start=idx + 1):
            yield Delivery(event=part, cursor=i, replayed=True)
        if q is None:
            return
        try:
            while True:
                item = await q.get()
                if item is None:
                    return
                yield item
        finally:
            async with self._lock:
                self._subscribers.discard(q)

    def __aiter__(self) -> AsyncIterator[Delivery]:
        """Default subscription: backfill from the start + continue live.

        On the main path (the client that just started the run), the log is
        still empty at this point, so this is equivalent to pure live
        streaming — no extra cost.
        """
        return self.subscribe(0)

    async def wait(self) -> RunStatus:
        """Wait for this round to finish, and return its final status."""
        await self._finished.wait()
        return self._status

    # ── interrupt ────────────────────────────────────────────────────────────

    def on_stop(self, fn: Any) -> None:
        """Register a stop listener (sync or async callable). Called once,
        in order, the first time ``stop()`` takes effect.

        Purpose: propagate "this round has stopped" to **in-flight** external
        execution — checkpoint polling can only stop at a boundary, but a
        long-running tool that's actually executing right now (e.g. a
        subprocess on the product layer's execution side) needs someone to
        tell it to stop immediately.
        Exceptions from listeners are only logged, they don't affect the
        interrupt itself. Registering after it has already stopped fires
        immediately."""
        if self._stop_requested:
            self._fire_stop_listener(fn)
            return
        self._stop_listeners.append(fn)

    def _fire_stop_listener(self, fn: Any) -> None:
        try:
            result = fn()
            if asyncio.iscoroutine(result):
                asyncio.ensure_future(result)
        except Exception:
            logger.exception("run stop listener failed run=%s", self.id)

    async def stop(self) -> None:
        """Request an interrupt.

        **Only sets a flag, never force-kills** — the executor will notice at
        its next checkpoint and wind down normally (persist state, emit a
        termination event). Force-killing would leave state half-written,
        which is exactly what this avoids. Idempotent; fires the ``on_stop``
        listeners the first time it takes effect (propagating to in-flight
        external execution)."""
        if self._stop_requested:
            return
        self._stop_requested = True
        for fn in self._stop_listeners:
            self._fire_stop_listener(fn)

    @override
    def __repr__(self) -> str:
        return (f"<Run {self.id} session={self.session_id} {self._status.value} "
                f"log={len(self._log)} subs={len(self._subscribers)}>")


class RunPool:
    """Registry of in-flight runs.

    Only holds runs that are **currently running** — so they can be found by
    session to interrupt, or so a second client can attach to the same
    stream. Removed via :meth:`discard` once finished; the single source of
    truth for conversation state always lives in the database, never here.

    Indexed by ``session_id``: only one round may be running at a time per
    conversation.
    """

    def __init__(self) -> None:
        self._by_session: Dict[str, Run] = {}
        self._by_id: Dict[str, Run] = {}

    def add(self, run: Run) -> None:
        if run.session_id:
            self._by_session[run.session_id] = run
        self._by_id[run.id] = run

    def find(self, session_id: str) -> Optional[Run]:
        """Find the round currently running for this conversation."""
        return self._by_session.get(session_id)

    def get(self, run_id: str) -> Optional[Run]:
        """Look up a run by its exact id (clients pass this back on
        reconnect)."""
        return self._by_id.get(run_id)

    def discard(self, run: Run) -> None:
        self._by_id.pop(run.id, None)
        if self._by_session.get(run.session_id) is run:
            self._by_session.pop(run.session_id, None)

    def active_sessions(self) -> List[str]:
        return list(self._by_session)

    def active(self) -> List["Run"]:
        """A snapshot of every in-flight Run (used by shutdown cleanup /
        health-check sweeps)."""
        return list(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)


__all__ = [
    "Run",
    "RunPool",
    "RunStatus",
    "Delivery",
    "Coalescer",
    "PassthroughCoalescer",
    "LogWrite",
]
