"""Runtime — the public facade object for flops_agent.

Analogous to FastAPI's ``app`` or redis-py's ``Redis`` client: **construct once,
serve every request**.

    # at process startup
    runtime = Runtime(llm=my_llm, tools=[get_weather, search], database=my_db)

    # per request
    session = await runtime.load_session(conv_id, owner_id=uid, keys=req_keys)
    run = runtime.start(session, Query.text("please look at this file"))
    async for delivery in run:
        yield to_sse(delivery.event)

**Rule of thumb: everything injected into Runtime must be stateless** — it must
not remember anything about a single request. Anything that varies per request
belongs on ``Session`` / ``Query`` / ``Run``. Break this rule and Runtime
degrades into "construct a new one per request", at which point it stops being
a facade at all.

For the same reason, ``runner`` stores a **class**, not an instance: a run's
in-flight state needs a container, and only a fresh instance per run gives you
a usable ``self``. Storing an instance would force you to thread state through
a shared dict instead.
"""
from __future__ import annotations

from typing_extensions import override
import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator, Callable, Dict, List, Mapping, Optional, Tuple, Type, Union, cast

from .execution import Coalescer, Run, RunPool, SessionRunActiveError
from flops_agent.entities.query import Query
from .runner import Runner
from flops_agent.seams.database import Database, sync_session
from flops_agent.tools.registry import DEFAULT_REGISTRY, ToolRegistry
from flops_agent.tools.schema import normalize_tools
from flops_agent.seams.executor import DefaultToolExecutor, ToolExecutor
from flops_agent.seams.inbox import Inbox
from flops_agent.seams.llm_client import LLMStreamClient
from flops_agent.seams.run_store import RunStore
from flops_agent.entities.session import Session
from flops_agent.entities.agent import Agent
from flops_agent.wire import WireCodec


logger = logging.getLogger(__name__)


#: Transient exceptions recognized by name by the built-in conservative classifier
#: (decoupled from any specific provider SDK — matched by exception class name;
#: a product that wants full control injects its own classifier via
#: :attr:`LLMStreamRetryPolicy.is_retriable`).
_RETRYABLE_STREAM_EXC_NAMES = frozenset({
    "MidStreamFallbackError",   # litellm: retriable disconnect after the stream has started (status-503-like)
    "APIConnectionError",
    "APIConnectionTimeoutError",
    "ReadTimeout",
    "ConnectTimeout",
    "RemoteProtocolError",
})


@dataclass
class LLMStreamRetryPolicy:
    """Retry policy for a mid-stream failure of an LLM streaming step.

    This only covers failures that happen *after* the stream has started
    producing output — transient errors before the first byte are already
    handled by the product-layer LLM seam's own backoff-and-retry (that's the
    seam's contract). The two layers have independent budgets and don't
    substitute for each other.

    The semantics are **discard-and-resend-the-whole-step**, not resume-from-
    checkpoint: a half-written tool_call JSON blob can't be resumed, and
    partial resumption isn't reliable anyway. Before resending, the framework
    emits ``LLMStreamRetrying`` and calls ``Runner.on_llm_stream_retry``, so
    the product layer and client can reset any half-built downstream state.

    ``is_retriable(exc)`` decides whether an exception is worth retrying; if
    left as None, the built-in conservative default applies: ``status_code >=
    500`` or 408/425/429, or the exception's class name matches the transient
    name list.
    """

    max_attempts: int = 3          # includes the first attempt, i.e. at most max_attempts - 1 retries
    backoffs: Tuple[float, ...] = (0.8, 2.0)   # before the i-th retry, sleep backoffs[min(i-1, len-1)] seconds
    is_retriable: Optional[Callable[[BaseException], bool]] = None

    def retriable(self, exc: BaseException) -> bool:
        if isinstance(exc, asyncio.CancelledError):
            return False
        if self.is_retriable is not None:
            return bool(self.is_retriable(exc))
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and (status >= 500 or status in (408, 425, 429)):
            return True
        return type(exc).__name__ in _RETRYABLE_STREAM_EXC_NAMES


class Runtime:
    """Long-lived agent runtime.

    Args:
        llm: an object whose ``acompletion(**kwargs)`` returns an async
            iterator of chunks.
        tools: list of tools. Either plain Python callables (their signature
            is auto-converted to a schema) or OpenAI tool schema dicts
            (can also be written as ``{"schema": ..., "fn": ...}``).
        database: the session persistence backend. Hot/cold tiering, key
            layout, and encryption/decryption are all its own implementation
            details.
        executor: custom tool dispatch; by default calls the callables in
            ``tools`` directly.
        runner: a subclass of ``Runner`` (**a class, not an instance**) — this
            is where a product hangs its own middleware.
        coalescer: a ``Coalescer`` class or factory, one per Run; by default
            no coalescing happens.
        session_class: a subclass of ``Session``, instantiated by
            ``load_session``.
        inbox: the delivery queue for out-of-turn input
            (:class:`~flops_agent.seams.inbox.Inbox`). Messages that arrive
            while the agent is running are delivered through it at one of two
            boundaries (``deliver="turn"`` waits for the turn to wrap up;
            ``"step"`` inserts at the nearest loop checkpoint). Defaults to
            the in-process :class:`MemoryInbox`; multi-process deployments
            inject a shared-storage implementation.
        wire: the wire serializer (:class:`~flops_agent.wire.WireCodec`). The
            default is the official standard implementation — unlike
            database's "minimal working reference", the standard wire codec
            is meant to be used directly in production; the injection point
            exists so that "change one kind of frame" or "swap out the whole
            protocol" has a legitimate place to happen. ``sse_stream()``
            serializes through it.
        model: the model id, merged into every request.
        llm_stream_retry: retry policy for a mid-stream failure of the LLM
            streaming step (:class:`LLMStreamRetryPolicy`). None disables it
            (failures propagate immediately).
        **completion_kwargs: extra keyword arguments passed through to every
            ``acompletion`` call.
    """

    def __init__(
        self,
        *,
        llm: Optional[LLMStreamClient] = None,
        tools: Optional[List[Any]] = None,
        database: Optional[Database] = None,
        executor: Optional[ToolExecutor] = None,
        agent: Optional[Agent] = None,
        runner: Optional[Type[Runner]] = None,
        coalescer: Optional[Union[Coalescer, Callable[[], Coalescer]]] = None,
        session_class: Optional[Type[Session]] = None,
        run_class: Optional[Type[Run]] = None,
        run_store: Optional[RunStore] = None,
        inbox: Optional[Inbox] = None,
        wire: Optional[WireCodec] = None,
        model: Optional[str] = None,
        rescue_silent_reply: bool = True,
        reply_rescue_plan: Optional[Dict[str, Any]] = None,
        silent_reply_max_rescues: int = 1,
        llm_stream_retry: Optional[LLMStreamRetryPolicy] = None,
        registry: Optional[ToolRegistry] = None,
        **completion_kwargs: Any,
    ):
        self.llm = llm
        self.tools = tools
        # The flat ``tools=[fn | schema | {"schema", "fn"}]`` form is sugar for the
        # registry: it gets registered into the root package ``/tools`` (the root
        # package is always visible, no need to open it). When no registry is given,
        # use a **private** registry rather than polluting the module-level default.
        schemas, fn_map = normalize_tools(tools or [])
        if registry is None and schemas:
            registry = ToolRegistry()
        #: Tool registry (packages + tools). None = the module-level default instance;
        #: multiple Runtimes in the same process can each hold their own.
        self.registry: ToolRegistry = registry if registry is not None else DEFAULT_REGISTRY
        for schema in schemas:
            name = str(cast(Dict[str, Any], schema.get("function") or {}).get("name") or "")
            if name:
                self.registry.register_tool("/tools", name, schema, fn_map.get(name))
        self.database = database
        #: Tool-execution seam. Defaults to registry-based dispatch (DefaultToolExecutor);
        #: a product swaps in its own (e.g. dispatching over a WS to an executor process).
        self.executor: ToolExecutor = executor if executor is not None else DefaultToolExecutor()
        self.agent = agent
        self.runner: Type[Runner] = runner or Runner
        self.coalescer = coalescer
        self.session_class: Type[Session] = session_class or Session
        self.run_class: Type[Run] = run_class or Run
        self.run_store = run_store
        if inbox is None:
            from flops_agent.seams.inbox import MemoryInbox
            inbox = MemoryInbox()
        self.inbox = inbox
        if wire is None:
            from flops_agent.wire import WireCodec
            wire = WireCodec()
        #: Wire-serializer injection slot. Defaults to the **official standard**
        #: WireCodec (unlike, say, database's "minimal reference" — this one is
        #: used directly in production); a subclass overrides a single method to
        #: change one kind of frame shape, or injects an entirely custom codec.
        #: Framework call site: sse_stream().
        self.wire = wire
        self.model = model
        #: Silent-reply rescue (a turn that ended with the model thinking but not
        #: speaking gets one guided resend; see Runner.arm_reply_rescue).
        #: rescue_silent_reply=False disables the whole mechanism; reply_rescue_plan
        #: declares static prefill capability (None = fall back to a trailing system
        #: hint; override Runner.reply_rescue_plan to choose dynamically per model);
        #: silent_reply_max_rescues caps rescues per turn.
        self.rescue_silent_reply = rescue_silent_reply
        self.reply_rescue_plan = reply_rescue_plan
        self.silent_reply_max_rescues = silent_reply_max_rescues
        #: Retry policy for a mid-stream failure of the LLM streaming step
        #: (None = disabled, failures propagate immediately; see Runner.stream_llm).
        self.llm_stream_retry = llm_stream_retry
        self.completion_kwargs = completion_kwargs
        self.runs = RunPool()
        #: Optional product-layer hooks for changes to the session's active-run marker
        #: (wired up at startup; same style as database/run_store).
        #: marked(session, run_id): called after the marker is actually written
        #: (re-entering with the same run does not trigger it).
        #: cleared(session, run_id, reason): called after the marker is actually
        #: removed. reason: "finished" = run reached a terminal state, "stale" =
        #: stale-pointer cleanup, anything else is product-defined. A product hangs
        #: its own semantics here (unread markers, broadcasts); the hooks are called
        #: synchronously — schedule your own create_task for async work.
        self.on_session_run_marked: Any = None
        self.on_session_run_cleared: Any = None

    # ── Per-session run-state convenience surface ───────────────────────────

    def active_run(self, session_id: str, *, owner_id: str = "") -> Optional[str]:
        """"Is a run currently active for this session?" → the active run_id, or None.

        The in-process pool is checked first (authoritative); cross-process
        falls back to ``RunStore.get_latest_run_id`` plus a status check
        (anything other than the terminal vocabulary done/stopped/failed
        counts as alive). If metadata can't be found we don't guess — we
        return None (the opposite conservative direction from guard-clearing:
        queries would rather under-report, clearing would rather not clear)."""
        run = self.runs.find(session_id)
        if run is not None and not getattr(run, "done", False):
            return run.id
        store = self.run_store
        if store is None:
            return None
        getter = getattr(store, "get_latest_run_id", None)
        if getter is None:
            return None
        try:
            rid = str(getter(owner_id, session_id) or "").strip()
            if not rid:
                return None
            meta = store.get_meta(rid)
        except Exception:
            return None
        if meta is None:
            return None
        # For "is a run active" queries, suspended counts as finished (the process
        # side has already wrapped up and is waiting for another request to resume
        # it); for guard-clearing (_run_seems_live) it counts as alive — the two
        # directions are deliberately different.
        alive = str(getattr(meta, "status", "") or "") not in (
            "done", "stopped", "failed", "suspended"
        )
        return rid if alive else None

    async def stop_session(self, session_id: str, *, owner_id: str = "") -> Optional[str]:
        """Interrupt by session: in-process, find → ``run.stop()``; cross-process,
        write the stop intent to the store via the latest-run index (the other
        process's stop watcher will translate that intent into an in-memory
        flag). Returns the interrupted run_id, or None if there's no active run."""
        run = self.runs.find(session_id)
        if run is not None and not getattr(run, "done", False):
            await run.stop()
            return run.id
        rid = self.active_run(session_id, owner_id=owner_id)
        if not rid or self.run_store is None:
            return None
        try:
            self.run_store.request_stop(rid)
        except Exception:
            logger.exception("stop_session request_stop failed run=%s", rid)
            return None
        return rid

    async def sse_stream(self, run: Any, *, from_cursor: int = 0):
        """The standard endpoint body: subscribe to a run and yield SSE byte
        lines (serialized through the ``self.wire`` slot).

            return StreamingResponse(runtime.sse_stream(run))

        Reconnection: the client sends back the last ``cursor`` it received as
        ``from_cursor``. If a product needs to do something else between
        frames (shutdown signals, heartbeats), write your own loop — this
        method is just the one-liner for the most common shape."""
        async for delivery in run.subscribe(from_cursor=from_cursor):
            yield self.wire.delivery_to_sse(delivery)

    # ── Session active-run marker (framework is the sole owner; motivation = query speed, see Session field comments) ──

    def _patch_session_meta(self, session: Session, fields: Dict[str, Any]) -> None:
        """Best-effort surgical write to disk (``patch_meta``; a value of None
        deletes the field). Never a full save — a full SET would overwrite
        concurrent writes with a stale snapshot, wiping out an active-run
        pointer another run just wrote."""
        database = self.database
        if database is None:
            return
        patch = getattr(database, "patch_meta", None)
        if patch is None:
            return
        try:
            patch(session.session_id, dict(fields), owner_id=session.owner_id)
        except Exception:
            logger.exception(
                "patch session meta failed session=%s fields=%s",
                session.session_id, list(fields),
            )

    def _mark_session_active_run(self, session: Session, run_id: str) -> None:
        """A run starts: write the marker onto session meta and persist it.

        Re-entering with the same run_id (recovery reuses the old run_id when
        resuming after a process restart) does not rewrite it — this preserves
        the original started_at, so the recovery action doesn't reset the
        frontend's elapsed-time display."""
        try:
            f, sf = session.active_run_field, session.active_run_started_field
            if session.meta is None:  # pyright: ignore[reportUnnecessaryComparison] -- guards against a subclass that skipped __init__ assignment
                session.meta = {}
            if str(session.meta.get(f) or "").strip() == str(run_id):
                return
            now_iso = datetime.now().isoformat()
            session.meta[f] = run_id
            session.meta[sf] = now_iso
            self._patch_session_meta(session, {f: run_id, sf: now_iso})
            if self.on_session_run_marked is not None:
                try:
                    self.on_session_run_marked(session, run_id)
                except Exception:
                    logger.exception("on_session_run_marked failed session=%s", session.session_id)
        except Exception:
            logger.exception("mark active run failed session=%s", session.session_id)

    def _run_seems_live(self, run_id: str) -> bool:
        """Conservative liveness check used by guard-clearing: if we can't
        prove it's dead, treat it as alive.

        A hit in the in-process pool with done not set → alive; readable
        RunStore meta with a terminal-vocabulary status (done/stopped/failed)
        → dead; everything else (missing meta, unknown status, no store) →
        alive. The conservative direction would rather leave a stale pointer
        behind (a product's deep-cleanup pass can catch it later) than ever
        mistakenly delete a concurrent new run's marker."""
        rid = str(run_id or "").strip()
        if not rid:
            return False
        pooled = self.runs.get(rid)
        if pooled is not None:
            return not getattr(pooled, "done", False)
        store = self.run_store
        if store is None:
            return True
        try:
            meta = store.get_meta(rid)
        except Exception:
            return True
        if meta is None:
            return True
        return str(getattr(meta, "status", "") or "") not in ("done", "stopped", "failed")

    def _clear_session_active_run(
        self, session: Session, run_id: str, *, reason: str = "finished"
    ) -> None:
        """A run reaches a terminal state: guard-clear the marker. This trusts
        the current value in the database (not this run's in-memory snapshot)
        because a concurrent new run may already have overwritten the marker
        with its own:

        * current value == this run → clear it;
        * current value points at another run: still alive → leave it alone;
          dead → clear it anyway (prevents a stale leftover from sticking around);
        * current value is already empty → nothing to do."""
        try:
            f, sf = session.active_run_field, session.active_run_started_field
            stored = ""
            if self.database is not None:
                try:
                    meta_now = self.database.load_meta(
                        session.session_id, owner_id=session.owner_id
                    ) or {}
                    stored = str(meta_now.get(f) or "").strip()
                except Exception:
                    stored = str(session.meta.get(f) or "").strip()
            else:
                stored = str(session.meta.get(f) or "").strip()
            if not stored:
                return
            if stored != str(run_id) and self._run_seems_live(stored):
                return
            if session.meta is not None:  # pyright: ignore[reportUnnecessaryComparison] -- guards against a subclass that skipped __init__ assignment
                session.meta.pop(f, None)
                session.meta.pop(sf, None)
            self._patch_session_meta(session, {f: None, sf: None})
            if self.on_session_run_cleared is not None:
                try:
                    self.on_session_run_cleared(session, stored, reason)
                except Exception:
                    logger.exception("on_session_run_cleared failed session=%s", session.session_id)
        except Exception:
            logger.exception("clear active run failed session=%s", session.session_id)

    def clear_session_active_run(
        self,
        session_id: str,
        *,
        owner_id: str = "",
        run_id: str = "",
        reason: str = "cleared",
    ) -> None:
        """Explicit product-layer entry point for clearing the marker — cleanup
        outside a run's own lifecycle goes through here (sanitizing stale
        pointers, giving up on recovery, forced abort); uses the same guard
        logic as terminal-state clearing. ``run_id`` may be empty: empty means
        "clear it if the run the current value points to is dead" (pure stale
        cleanup)."""
        session = self.session_class(session_id, owner_id=owner_id)
        self._clear_session_active_run(session, run_id, reason=reason)

    # ── Session suspended marker (same family as the active marker; see Session.suspended_field comments) ──

    # ── Tool packages: session-level toggle (framework owns the write, persisted via patch_meta) ──

    def open_packages(self, session: Session, package_paths: Any) -> Dict[str, Any]:
        """Open one or more tool packages (additive, doesn't close existing
        ones). Paths must exist in this Runtime's registry and have tools;
        ``/tools`` is the root navigation domain and doesn't count as a
        package. Adds exactly the requested packages, no cascading into
        subpackages (subpackages must be opened individually). Returns a
        result dict meant for the model to read."""
        paths, err = self._normalize_package_paths(package_paths, must_exist=True)
        if err is not None:
            return err
        if not paths:
            return {"success": False, "error": "No valid tool package paths to open were provided"}
        opened = set(session.opened_packages)
        opened.update(paths)
        result = sorted(opened)
        self._set_opened_packages(session, result)
        return {
            "success": True,
            session.opened_packages_field: result,
            "message": f"Opened {len(paths)} tool package(s) ({len(result)} currently open)",
        }

    def close_packages(self, session: Session, package_paths: Any) -> Dict[str, Any]:
        """Close one or more tool packages (removes them from the open set).
        Removes exactly the given packages, no cascading into subpackages."""
        paths, err = self._normalize_package_paths(package_paths, must_exist=False)
        if err is not None:
            return err
        opened = set(session.opened_packages)
        for p in paths:
            opened.discard(p)
        result = sorted(opened)
        self._set_opened_packages(session, result)
        return {
            "success": True,
            session.opened_packages_field: result,
            "message": f"Closed {len(paths)} tool package(s) ({len(result)} currently open)",
        }

    def _normalize_package_paths(
        self, package_paths: Any, *, must_exist: bool,
    ) -> Tuple[List[str], Optional[Dict[str, Any]]]:
        if isinstance(package_paths, str):
            package_paths = [package_paths]
        if not isinstance(package_paths, list) or not all(isinstance(p, str) for p in cast(List[Any], package_paths)):
            return [], {"success": False, "error": "package_paths must be a list of strings"}
        out: List[str] = []
        for raw in cast(List[str], package_paths):
            p = (raw or "").strip()
            if not p or p == "/tools":
                continue
            if must_exist:
                if not p.startswith("/tools/"):
                    return [], {"success": False, "error": f"Invalid tool package path: {p} (must start with /tools/)"}
                if not self.registry.has_package(p):
                    return [], {"success": False, "error": f"Tool package does not exist or is empty: {p}"}
            out.append(p)
        return out, None

    def _set_opened_packages(self, session: Session, paths: List[str]) -> None:
        session.set_opened_packages(paths)
        self._patch_session_meta(session, {session.opened_packages_field: list(paths)})

    def mark_session_suspended(self, session: Session, marker: Dict[str, Any]) -> None:
        """Write "this session is suspended, waiting on the user's call" onto
        session meta plus a surgical write to disk.

        Called by the product layer at its suspend decision point (the
        payload is product-defined, e.g. which tool_call it's waiting on, the
        question text); if it isn't called, ``Runner.drive``'s terminal-state
        handling auto-fills it in from ``suspend_marker``."""
        try:
            f = session.suspended_field
            if session.meta is None:  # pyright: ignore[reportUnnecessaryComparison] -- guards against a subclass that skipped __init__ assignment
                session.meta = {}
            payload = dict(marker or {})
            session.meta[f] = payload
            self._patch_session_meta(session, {f: payload})
        except Exception:
            logger.exception("mark session suspended failed session=%s", session.session_id)

    def _clear_session_suspended(
        self, session: Session, run_id: str = "", *, reason: str = "resolved"
    ) -> None:
        """Guard-clear the suspended marker (trusts the current database value):

        * the marker belongs to this run (or can no longer be attributed to
          any run) → clear it;
        * the marker belongs to another run and that run is still "alive"
          (running or suspended both count) → leave it alone.

        Every run that ends normally passes through here — this is what lets
        zombie markers (pointing at a dead run but never cleared, which would
        otherwise block downstream triggers) self-heal, so a product doesn't
        need to write its own sweeper."""
        try:
            f = session.suspended_field
            stored: Any = None
            if self.database is not None:
                try:
                    meta_now = self.database.load_meta(
                        session.session_id, owner_id=session.owner_id
                    ) or {}
                    stored = meta_now.get(f)
                except Exception:
                    stored = session.meta.get(f)
            else:
                stored = session.meta.get(f)
            if not stored:
                return
            marker_rid = str(cast(Dict[str, Any], stored).get("run_id") or "").strip() if isinstance(stored, dict) else ""
            if (
                run_id
                and marker_rid
                and marker_rid != str(run_id)
                and self._run_seems_live(marker_rid)
            ):
                return
            if session.meta is not None:  # pyright: ignore[reportUnnecessaryComparison] -- guards against a subclass that skipped __init__ assignment
                session.meta.pop(f, None)
            self._patch_session_meta(session, {f: None})
        except Exception:
            logger.exception("clear session suspended failed session=%s", session.session_id)

    def settle_session_markers(
        self, session: Session, run_id: str, suspend_marker: Optional[Dict[str, Any]],
    ) -> None:
        """Settle both session markers when a turn wraps up (``Runner.drive``
        calls this after ``run.finish``).

        * Active-run marker: guard-cleared (never mistakenly deletes a
          concurrent new run's marker).
        * Suspended marker: this turn suspended (``suspend_marker`` is not
          None) → back-fill it if the product layer didn't already mark it at
          its decision point; if already marked, don't overwrite it (the
          product's own payload is richer). Normal completion → guard-clear,
          which lets a zombie marker pointing at a dead run self-heal.
        """
        self._clear_session_active_run(session, run_id)
        try:
            if suspend_marker is not None:
                if suspend_marker and not (session.meta or {}).get(session.suspended_field):
                    payload = dict(suspend_marker)
                    payload.setdefault("run_id", run_id)
                    self.mark_session_suspended(session, payload)
            else:
                self._clear_session_suspended(session, run_id)
        except Exception:
            logger.exception("suspend marker lifecycle failed run=%s", run_id)

    def clear_session_suspended(
        self,
        session_id: str,
        *,
        owner_id: str = "",
        run_id: str = "",
        reason: str = "resolved",
    ) -> None:
        """Explicit product-layer entry point for clearing the suspended
        marker (resume re-entry, self-heal on read). ``run_id`` empty =
        unconditional clear (the caller has already confirmed the marker
        should go — a suspended run itself counts as "alive" in the store, so
        the guarded path with a run_id would hold back; an explicit clear is
        exactly how you override that)."""
        session = self.session_class(session_id, owner_id=owner_id)
        self._clear_session_suspended(session, run_id, reason=reason)

    # ── Out-of-turn input delivery ───────────────────────────────────────────

    def deliver(self, session_id: str, message: Dict[str, Any], *, when: str = "turn") -> None:
        """Deliver a message to a (possibly currently running) session.

        ``when="turn"`` waits for the current work to wrap up and then
        resumes as a new user turn; ``when="step"`` inserts into the current
        work at the nearest loop checkpoint. If the session has no active
        run, the message sits in the inbox and gets delivered at the first
        boundary of the next run — see :mod:`flops_agent.seams.inbox` for the
        full delivery semantics.

        Convenience surface: equivalent to
        ``runtime.inbox.push(session_id, message, deliver=when)``; a custom
        Inbox implementation without a ``push`` method should be enqueued via
        its own API instead.
        """
        push = getattr(self.inbox, "push", None)
        if push is None:
            raise TypeError(
                f"{type(self.inbox).__name__} has no push(); enqueue via its own API"
            )
        push(session_id, message, deliver=when)

    # ── Session loading ───────────────────────────────────────────────────────

    def load_session_sync(
        self,
        session_id: str,
        *,
        owner_id: str = "",
        keys: Optional[Mapping[str, Any]] = None,
        create_if_missing: bool = True,
    ) -> Optional[Session]:
        """Restore a conversation from ``database``.

        Args:
            keys: **decryption keys**. Under zero-knowledge encryption the
                server never holds the keys — they're sent up by the client
                with every request, so they must be passed in at this loading
                step, otherwise the backend can only read back ciphertext.
                Their shape is defined by the backend.
            create_if_missing: if the session doesn't exist, return an empty
                one (rather than ``None``).

        This method **always** reads from database, never checks the
        ``runs`` pool: the pool exists only to find the turn that's
        "currently running" so it can be interrupted or streamed from —
        conversation state's single source of truth is database.

        **Sync variant** (for scripts, tests, or anywhere already running on
        a worker thread). Async services should use :meth:`load_session` —
        loading can be as heavy as a full decryption pass, and whether it
        blocks the event loop shouldn't depend on every product remembering
        to wrap it in to_thread.
        """
        meta: Optional[Mapping[str, Any]] = None
        messages: List[Dict[str, Any]] = []
        exists = False
        if self.database is not None:
            # Assembled from the protocol's **fine-grained** primitives, rather than
            # requiring the backend to implement its own load_session. The fine
            # granularity is deliberate: a backend only needs to implement a few small
            # operations (read a slice of messages, append, replace by index), and the
            # framework does the "assemble it into a conversation" work — which is the
            # same for every product — exactly once.
            meta = self.database.load_meta(session_id, owner_id=owner_id, keys=keys)
            # "Exists" can't be determined from meta alone: a conversation can perfectly
            # well have messages while meta is empty (no title set yet, no device bound).
            # Trusting meta alone would make those sessions come back empty on reload.
            count = self.database.count_messages(session_id, owner_id=owner_id)
            exists = meta is not None or count > 0
            if exists:
                messages = list(
                    self.database.load_messages(session_id, owner_id=owner_id, keys=keys) or []
                )
        if not exists:
            if not create_if_missing:
                return None
        meta = meta or {}
        return self.session_class(
            session_id,
            owner_id=owner_id,
            messages=messages,
            meta=dict(meta),
        )

    async def load_session(
        self,
        session_id: str,
        *,
        owner_id: str = "",
        keys: Optional[Mapping[str, Any]] = None,
        create_if_missing: bool = True,
    ) -> Optional[Session]:
        """Async loading (a thread-pool wrapper around :meth:`load_session_sync`).

        "Don't let blocking work stall the event loop" is a service framework's
        job to handle: loading can be as heavy as a SQLite scan plus a full
        decryption pass, so the framework offloads it to the thread pool and
        the product layer just ``await``s it. Contextvars (e.g. the key
        environment for zero-knowledge decryption) are carried across
        automatically by to_thread.
        """
        return await asyncio.to_thread(
            self.load_session_sync,
            session_id,
            owner_id=owner_id,
            keys=keys,
            create_if_missing=create_if_missing,
        )

    def save_session_sync(self, session: Session, *, keys: Optional[Mapping[str, Any]] = None) -> None:
        """Write conversation state back to ``database`` (``keys`` same as
        :meth:`load_session`). Sync variant."""
        if self.database is None:
            return
        # First make sure the session exists — the protocol requires create_session
        # to be idempotent, so calling it repeatedly has no side effects. Skipping
        # this could leave the backend with only messages and no session record,
        # depending on how it implements append.
        self.database.create_session(
            session.session_id, owner_id=session.owner_id, meta=dict(session.meta or {})
        )
        # Also goes through the fine-grained primitives: sync_session only appends what's
        # new, it never rewrites the whole conversation — a full rewrite is a real cost on
        # an encrypted backend (seconds per call for a conversation with thousands of messages).
        sync_session(self.database, session, keys=keys)
        if session.meta:
            self.database.patch_meta(
                session.session_id, dict(session.meta),
                owner_id=session.owner_id, keys=keys,
            )

    async def save_session(self, session: Session, *, keys: Optional[Mapping[str, Any]] = None) -> None:
        """Async write-back (a thread-pool wrapper around
        :meth:`save_session_sync`, for the same reasons as load)."""
        await asyncio.to_thread(self.save_session_sync, session, keys=keys)

    # ── Running a turn ─────────────────────────────────────────────────────────

    def start(
        self,
        session: Session,
        query: Optional[Query] = None,
        *,
        run_id: Optional[str] = None,
        keys: Optional[Mapping[str, Any]] = None,
        context: Any = None,
    ) -> Run:
        """Start a run, **returning a handle immediately** (does not wait for
        it to finish).

        Execution advances on its own in a background task: it keeps running
        even with no subscribers, and a client disconnect doesn't stop it.
        The caller gets a :class:`Run` back and can subscribe, interrupt, or
        reconnect by cursor at will.

        Omitting ``query`` means "continue from the session's current
        state" (both "regenerate" and "continue writing" go through this
        path — the difference between them is determined by session state,
        no separate flag needed).

        ``keys``: this run's decryption keys, passed through to
        ``runner.keys`` (the slot persist already uses).
        ``context``: the product layer's context for this run (e.g. the
        original request object), passed through to ``runner.context``; the
        framework **does not interpret it** — same neutrality as the
        Database protocol.
        """
        existing = self.runs.find(session.session_id)
        if existing is not None and not existing.done:
            raise SessionRunActiveError(session.session_id, existing.id)
        # run_class is the same kind of injection point as session_class: a
        # product's Run subclass can carry its own product fields; persistence
        # (RunStore) and lifecycle are handled by the base class mechanics.
        run = self.run_class(
            run_id or uuid.uuid4().hex,
            session_id=session.session_id,
            owner_id=session.owner_id,
            coalescer=self._new_coalescer(),
            store=self.run_store,
        )
        runner = self.runner(runtime=self, session=session, query=query, run=run)
        if keys is not None:
            runner.keys = keys
        runner.context = context
        self.runs.add(run)
        # Session active-run marker (the framework is the sole owner): written onto
        # session.meta and surgically persisted via patch_meta, so cross-process
        # readers (hydrate / sidebar snapshots) can tell a session is "running" just
        # by reading it. Cleared on terminal state by Runner.drive's guard-clearing.
        # Products should not write these two fields themselves — multiple writers
        # would inevitably clobber each other.
        self._mark_session_active_run(session, run.id)

        async def _drive() -> None:
            try:
                await runner.drive()
            finally:
                self.runs.discard(run)

        run.task = asyncio.create_task(_drive())
        self._spawn_stop_watcher(run)
        return run

    #: Stop-watcher polling interval (seconds) — the maximum extra delay between an
    #: external "stop" request and the run actually beginning to wrap up.
    stop_poll_interval: float = 0.5

    def _spawn_stop_watcher(self, run: Run) -> None:
        """Translate a stop intent recorded in the store into ``run.stop()``
        (part of run supervision).

        A cross-process / cross-restart interrupt needs shared storage to
        carry the intent, since checkpoints only read an in-memory flag —
        this watcher is the courier between the two.
        Does nothing if the store doesn't implement stop semantics; a probe
        exception is **not treated as stopped** (a false positive would kill
        half-finished work unrecoverably, while a false negative only delays
        it to the next tick)."""
        probe = getattr(self.run_store, "stop_requested", None)
        if probe is None:
            return

        async def _watch() -> None:
            while not run.done:
                try:
                    if probe(run.id):
                        await run.stop()
                        return
                except Exception as e:
                    logger.warning("stop watcher probe failed (treated as not-stopped): %s", e)
                try:
                    await asyncio.wait_for(run.wait(), timeout=self.stop_poll_interval)
                    return
                except asyncio.TimeoutError:
                    pass

        asyncio.create_task(_watch(), name=f"stop-watch:{run.id}")

    # ── Resuming after a restart (part of run supervision) ──────────────────

    async def recover(self, resume: Any, *, on_gave_up: Any = None) -> int:
        """Re-launch runs that were interrupted by the previous process.

        The framework handles the orchestration: enumerate runs pending
        recovery in the store → clean up orphans with missing metadata →
        the ``mark_resuming`` circuit breaker (poison-pill runs are tripped
        by the store's own counting) → hand each run's meta off to the
        product layer's ``resume`` hook, scheduled in the background. **How
        to rebuild the session, where the keys come from, and how exactly to
        relaunch** are all deployment specifics that live entirely inside
        resume; when the circuit breaker gives up, ``on_gave_up(meta)`` is
        called so the product layer can clear its own external pointers.

        No-op if the store doesn't implement the recovery semantics
        (list_active_run_ids / mark_resuming).
        Returns the number of runs scheduled for resumption.
        """
        store = self.run_store
        if store is None:
            return 0
        lister = getattr(store, "list_active_run_ids", None)
        resumer = getattr(store, "mark_resuming", None)
        if lister is None or resumer is None:
            return 0
        try:
            run_ids = lister()
        except Exception:
            logger.warning("recover: list_active_run_ids failed", exc_info=True)
            return 0
        if not run_ids:
            return 0
        logger.info("recover: found %d run(s) to resume from previous process", len(run_ids))
        scheduled = 0
        for rid in run_ids:
            try:
                meta = store.get_meta(rid)
                if not meta:
                    deleter = getattr(store, "delete_run", None)
                    if deleter is not None:
                        deleter(rid)
                    continue
                if resumer(rid) == 0:
                    logger.warning("recover: gave up run=%s (resume budget exceeded)", rid)
                    if on_gave_up is not None:
                        try:
                            _res = on_gave_up(meta)
                            if asyncio.iscoroutine(_res) or asyncio.isfuture(_res):
                                await _res
                        except Exception:
                            logger.exception("recover: on_gave_up failed run=%s", rid)
                    continue
                logger.info("recover: scheduling resume run=%s", rid)
                asyncio.create_task(self._resume_one(resume, meta, rid))
                scheduled += 1
            except Exception:
                logger.exception("recover: scheduling failed run=%s", rid)
        return scheduled

    async def _resume_one(self, resume: Any, meta: Any, rid: str) -> None:
        try:
            await resume(meta)
        except Exception:
            logger.exception("recover: resume hook failed run=%s", rid)

    async def ask(
        self,
        text: str,
        *,
        session: Optional[Session] = None,
    ) -> AsyncIterator[Any]:
        """Convenience entry point for a one-off question-and-answer — use
        this for the minimal path, no need to build a Session first.

            async for event in runtime.ask("what's the weather today"):
                print(event)

        Yields **the event itself** rather than a :class:`Delivery`: a
        one-off ask doesn't involve reconnection, so a cursor would just be
        dead weight. Use :meth:`start` if you need one.
        """
        session = session or self.session_class(uuid.uuid4().hex)
        run = self.start(session, Query.text(text))
        async for delivery in run:
            yield delivery.event

    def _new_coalescer(self) -> Optional[Coalescer]:
        """One coalescer per Run — it's stateful (it accumulates pending
        content that hasn't formed a complete segment yet), so it can't be shared."""
        factory = self.coalescer
        if factory is None:
            return None
        return factory() if callable(factory) else factory

    # ── Interruption ──────────────────────────────────────────────────────────

    async def stop(self, session_id: str) -> bool:
        """Interrupt the turn currently running for this conversation.
        Returns whether an in-flight Run was found."""
        run = self.runs.find(session_id)
        if run is None:
            return False
        await run.stop()
        return True

    async def shutdown(self) -> int:
        """Graceful shutdown sweep: mark every in-flight run interrupted
        (recorded in the store) and wake up its subscribers.

        **Does not cancel the driving task or set a terminal state** — the
        task is reaped by the product layer's own process lifecycle (SIGTERM,
        event-loop shutdown, etc.); the interrupted marker in the store is
        what the recovery flow uses to find these runs again after a restart.
        A product calls this method from its own shutdown hook (alongside its
        own deployment specifics, like flushing staged keys). Returns the
        number of runs swept.
        """
        swept = 0
        for run in self.runs.active():
            if run.done:
                continue
            await run.interrupt_for_shutdown()
            swept += 1
        return swept

    def with_overrides(self, **kwargs: Any) -> "Runtime":
        """Derive a new runtime with a few configuration values changed
        (swap the model, change the step limit, etc). The original instance
        is left unchanged."""
        base: Dict[str, Any] = {
            "llm": self.llm, "tools": self.tools, "database": self.database,
            "executor": self.executor, "runner": self.runner,
            "coalescer": self.coalescer, "session_class": self.session_class,
            "run_class": self.run_class, "run_store": self.run_store,
            "model": self.model, "registry": self.registry,
            **self.completion_kwargs,
        }
        base.update(kwargs)
        derived = Runtime(**base)
        # A derived runtime **shares the parent's RunPool**: the semantics of deriving
        # are "same runtime, a few config values changed", and run supervision
        # (shutdown sweeping, runs.find for stream reattachment) must see every
        # in-flight run — if each derived instance had its own pool, the base
        # runtime's shutdown() would find nothing left to sweep.
        derived.runs = self.runs
        return derived

    @override
    def __repr__(self) -> str:
        return (f"<Runtime runner={self.runner.__name__} "
                f"tools={len(self.tools or [])} active_runs={len(self.runs)}>")


__all__ = ["Runtime", "LLMStreamRetryPolicy"]
