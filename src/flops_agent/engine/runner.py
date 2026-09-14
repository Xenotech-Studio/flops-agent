"""Runner —— the machine that drives a single run.

**One instance per run.** Products subclass it and override steps:

    class MyRunner(Runner):
        async def build_request(self):
            req = await super().build_request()
            req["messages"] = [self.system_message()] + req["messages"]
            return req

    runtime.runner = MyRunner        # assign the *class*, not an instance

Why "one instance per run" instead of "a process-level singleton plus a pile of
hooks": a single run accumulates a lot of intermediate state (the growing
content, the reasoning segment, this step's tool calls, timing marks). A
singleton has nowhere to put that state except a shared dict passed around
everywhere — which is exactly the thing we're trying to get away from.
With one instance per run, ``self`` is naturally the state container, and the
intermediate state becomes typed instance attributes.

The override points are laid out in the natural order of a run, each with a
default implementation that just works out of the box:

======================  ==========================================================
Step                     Responsibility
======================  ==========================================================
``accept_query()``       Land this turn's contribution into history (skip if none)
``build_request()``      History + tools -> LLM request parameters
``on_chunk()``           A raw chunk arrives (usage / finish_reason are only visible here)
``on_event()``           A parsed event is about to be delivered (rewrite or swallow it)
``consume_stream()``     Consume one attempt's full stream (retry orchestration stays in stream_llm)
``build_reply()``        Stream ends -> assemble the assistant message
``before_tool()``        Gate before tool execution (allow / rewrite / deny / suspend)
``execute_tool()``       Actual execution
``after_tool()``         Shape the result
``should_continue()``    Should we take another step?
``finalize()``           Wrap-up (persistence, etc.)
``on_llm_stream_retry()`` The stream failed mid-way and the whole step is about to be resent (reset half-finished downstream state)
======================  ==========================================================
"""
from __future__ import annotations

from typing_extensions import override
import asyncio
import json
import logging
import time
from datetime import datetime
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, cast, Set

from flops_agent.entities import events as _ev
from flops_agent.entities.contracts import FinishStreamChunk, JSONMapping, StreamChunk, ToolCall, ToolOutcome
from flops_agent.tools.registry import ToolContext
from flops_agent.seams.database import sync_session
from .execution import Run, RunStatus
from .interaction import UNSET, Interaction, InteractionKind, InteractionRequest, StepPlan, ToolAction, ToolGate
from .stream import StreamAccumulator
from flops_agent.tools.schema import result_to_content, tool_call_from_dict, tool_call_to_openai
from flops_agent.entities.query import Contributor, Query
from flops_agent.entities.session import Session

if TYPE_CHECKING:  # For static analysis only: runtime.py imports this module back, so it can't be a real import at runtime
    from flops_agent.entities.agent import Agent
    from .runtime import Runtime

logger = logging.getLogger(__name__)


def _result_has_error(result: object) -> bool:
    """Preserve the historical ``{"error": ...}`` failure convention."""
    record = cast(Dict[str, object], result) if isinstance(result, dict) else {}
    return record.get("error") is not None


#: Silent-reply rescue: the trailing nudge template for the system fallback channel
#: (``{reasoning}`` is substituted with the rescued reasoning).
SILENT_REPLY_NUDGE_TEMPLATE = (
    "Your previous step only produced internal reasoning and no visible reply — "
    "the user can't see your reasoning, so it looked like you didn't respond at all. "
    "Your reasoning at the time was:\n\n{reasoning}\n\n"
    "Now write your final reply directly as content and send it to the user; "
    "don't mention this reminder, and don't only produce reasoning again."
)

#: Silent-reply rescue: the cue sentence appended to the end of the original
#: reasoning on the ``reasoning_prefill`` channel.
SILENT_REPLY_REASONING_CUE = "Ok, now let me write the actual reply to the user."

#: Silent-reply rescue: the content prefix prefilled on the ``content_prefill`` channel.
SILENT_REPLY_CONTENT_PREFIX = "Sure, "


def _with_system(messages: List[Dict[str, Any]], text: str) -> List[Dict[str, Any]]:
    """Return a **new list** with the system message prepended; the original history
    is untouched — identity is a wire-layer concern and shouldn't be written into
    persisted history.

    If a system message already exists, merge into it instead of inserting another
    one: multiple system messages are handled inconsistently across providers (some
    merge them, some only honor the first, some error out) — whether the persona
    takes effect shouldn't depend on which model you happen to be using.
    """
    if messages and messages[0].get("role") == "system":
        existing = str(messages[0].get("content") or "")
        merged = f"{text}\n\n{existing}" if existing else text
        return [{**messages[0], "content": merged}] + list(messages[1:])
    return [{"role": "system", "content": text}] + list(messages)


class Runner:
    """A single run. The default implementation is a working, plain agent loop."""

    def __init__(
        self,
        *,
        runtime: "Runtime",
        session: Session,
        query: Optional[Query],
        run: Run,
    ):
        self.runtime = runtime
        self.session = session
        self.query = query
        self.run = run

        # ── State for this run (things that used to live scattered across a scratch dict) ──
        self.step = 0
        self.assistant_text = ""
        self.reasoning_text = ""
        self.finish_reason: Optional[str] = None
        self.usage: Optional[JSONMapping] = None
        self.tool_calls: List[ToolCall] = []
        self.stream_acc: Optional[StreamAccumulator] = None
        """The live accumulator for this step's streaming attempt (set by
        :meth:`stream_llm`, updated in place chunk by chunk).

        ``self.assistant_text`` / ``reasoning_text`` / ``tool_calls`` are only
        committed from it once the stream **finishes normally** (see the end of
        :meth:`stream_llm`) — if ``consume_stream`` returns ``False`` (interrupted)
        or raises ``CancelledError``, that commit never happens and the three
        fields stay at this step's reset values. If interruption / cancellation
        cleanup needs to know "how much has been accumulated so far", read this
        field (``.text`` / ``.reasoning`` / ``.tool_calls()``) instead of keeping a
        second parallel accumulator in ``on_chunk`` — that's duplicated bookkeeping
        maintained in two places that will eventually drift apart. Real incident:
        the product layer once hand-rolled a whole separate accumulator just for
        interruption snapshots."""
        #: Resumed dispatch: the calls dispatched this step were already dispatched
        #: by a previous process; ``{tool_call_id: dispatch record}``.
        #: Empty = normal dispatch. See :meth:`resume_dispatch_plan`.
        self.resuming: Dict[str, Dict[str, Any]] = {}
        self.suspend_marker: Optional[Dict[str, Any]] = None
        """Non-empty means this run stopped at a "waiting for the user to decide"
        point, with a final status of ``SUSPENDED``."""
        self.stopped_reason = ""
        """The reason ``plan_step`` decided to end here."""
        self.reply_rescue: Optional[Dict[str, Any]] = None
        """In-flight state for the silent-reply rescue (reasoning with no visible
        reply); see :meth:`arm_reply_rescue`."""
        self.reply_rescue_attempts = 0
        """How many times the silent-reply rescue has been armed this run (capped
        by ``runtime.silent_reply_max_rescues``)."""
        self.revision = 0
        """History revision number, incremented on every persist (sent to clients
        with HistoryChanged)."""
        self.keys: Optional[Mapping[str, Any]] = None
        """Decryption keys for this run (supplied by the request under
        zero-knowledge encryption; the framework only passes them through)."""
        self.context: Any = None
        """The product layer's context for this run (passed through from
        ``Runtime.start(context=…)``; the framework doesn't interpret it)."""

    # ── Convenience accessors ─────────────────────────────────────────────────

    @property
    def messages(self) -> List[Dict[str, Any]]:
        """The session history. Appended to in place — assistant / tool turns go
        straight into the session."""
        return self.session.messages

    async def emit(self, event: Any) -> None:
        """Emit an event (into the log + to online subscribers)."""
        await self.run.emit(event)

    # ── Main loop: normally not overridden ──────────────────────────────────

    async def drive(self) -> RunStatus:
        """Run this turn to completion. Called by ``Runtime`` from a background
        task; exceptions never escape.

        When the task is **cancelled** (product layer calling ``run.task.cancel()``
        / shutdown), we don't swallow it: we clean up and then re-raise, otherwise
        shutdown would hang. But the final status and subscriber wakeup must not be
        lost because of this — without ``finish``, subscribers would never get
        their sentinel. Session markers (active / suspended) are **not** cleared on
        the cancellation path: a run interrupted by shutdown is resumed by
        recovery, and the marker must still be there within the reload window. A
        user-initiated stop goes through ``stop_requested`` (inside the loop) and
        is cleared as usual.
        """
        status = RunStatus.DONE
        error: Optional[BaseException] = None
        error_message: Optional[str] = None
        cancelled = False
        try:
            await self.accept_query()
            await self.on_loop_start()
            status = await self.loop()
        except asyncio.CancelledError:
            cancelled = True
            status = RunStatus.STOPPED
            if self.run.stop_requested:
                # Explicit stop + hard cancel (product layer calls stop() then
                # task.cancel(), without waiting for this step to finish): clean up
                # the same way as the stop_requested path inside loop() — an
                # interruption snapshot + a Cancelled event. Cancellation caused by
                # shutdown (stop_requested not set) doesn't emit this: that's not
                # "the user stopped it", it's "the process is going away".
                try:
                    await self.on_cancelled()
                    await self.emit(_ev.Cancelled())
                except Exception:
                    logger.exception("on_cancelled failed run=%s", self.run.id)
        except Exception as e:                      # noqa: BLE001 —— the final status must land on the Run
            logger.exception("runner failed run=%s", self.run.id)
            status, error = RunStatus.FAILED, e
            try:
                error_message = self.describe_error(e)
            except Exception:
                logger.exception("describe_error failed run=%s", self.run.id)
                error_message = str(e) or type(e).__name__
            # The Error event is sent via Runner.emit (that's where the product
            # layer's serialization boundary lives); finish doesn't send it again.
            # The ordering matches the long-standing "emit frame -> finalize ->
            # finish" sequence: by the time finalize wraps up, the error has
            # already been reported to subscribers.
            try:
                await self.emit(_ev.Error(message=error_message, exc=e))
            except Exception:
                logger.exception("emit Error failed run=%s", self.run.id)
        try:
            await self.finalize(status)
        except Exception:
            logger.exception("runner finalize failed run=%s", self.run.id)
        await self.run.finish(status, error=error, error_message=error_message, emit_error=False)
        if cancelled:
            await self._safe_on_run_end(status)
            raise asyncio.CancelledError()
        # The active / suspended markers on the session are settled once this run
        # concludes (after finish: only then has RunStore recorded the final
        # status, so a guard checking liveness gets an accurate read). The
        # decision uses suspend_marker rather than status — it's the direct signal
        # for whether we're suspended.
        self.runtime.settle_session_markers(self.session, self.run.id, self.suspend_marker)
        await self._safe_on_run_end(status)
        return status

    async def _safe_on_run_end(self, status: RunStatus) -> None:
        try:
            await self.on_run_end(status)
        except Exception:
            logger.exception("on_run_end failed run=%s", self.run.id)

    async def loop(self) -> RunStatus:
        """The step loop: each iteration = pre-step guard -> run a step
        (:meth:`run_step`) -> ask whether this run has ended (:meth:`ending_after_step`).
        Returns the run's final status: DONE (concluded), STOPPED (stopped),
        SUSPENDED (waiting for the user to decide)."""
        while True:
            if self.run.stop_requested:            # Before the step: a stop was already requested, don't start a new one
                return await self._end_stopped()
            step_completed = await self.run_step()
            ending = await self.ending_after_step(step_completed)
            if ending is not None:
                return ending
            self.step += 1

    async def ending_after_step(self, step_completed: bool) -> Optional[RunStatus]:
        """After a step finishes, decide whether this run has ended. Returning a
        final status ends it; ``None`` means take another step.
        Priority order: interrupted mid-step -> suspended waiting on the user ->
        the step body explicitly concluded -> silent-reply rescue adds a step ->
        ``should_continue``. Normally not overridden."""
        if not step_completed:
            # Interrupted mid-stream or mid-dispatch — don't wait for the step to finish
            return await self._end_stopped()
        if self.suspend_marker is not None:
            return RunStatus.SUSPENDED             # Stopped waiting for the user to decide; resumes on another request
        if self.stopped_reason:
            await self.emit(_ev.LoopFinished(reason=self.stopped_reason))
            return RunStatus.DONE
        if self.reply_rescue is not None and not self.reply_rescue.get("applied"):
            # Silent-reply rescue is armed: skip the conclusion check and just
            # take another step — ``apply_reply_rescue`` will inject the nudge
            # into next step's request. The attempt cap is enforced at the arming
            # site, so this can't loop forever.
            return None
        if not await self.should_continue():
            await self.on_loop_end()
            await self.emit(_ev.LoopFinished(reason="stop"))
            return RunStatus.DONE
        return None

    async def _end_stopped(self) -> RunStatus:
        """Shared cleanup for being stopped: interruption cleanup hook + Cancelled
        event -> STOPPED."""
        await self.on_cancelled()
        await self.emit(_ev.Cancelled())
        return RunStatus.STOPPED

    async def run_step(self) -> bool:
        """One step: request from the LLM, consume the stream, land the assistant
        turn, dispatch tools.

        Returns ``False`` to signal a mid-step interruption — **interruption must
        be checked chunk by chunk**, not just at step boundaries: if the user hits
        stop and we still force this step to run to completion (possibly a tool
        call taking tens of seconds), it feels like the app is unresponsive.
        """
        self.assistant_text = ""
        self.reasoning_text = ""
        self.tool_calls = []
        self.finish_reason = None
        self.stream_acc = None

        # This step's plan: normally call the LLM; on resume / bridging, dispatch
        # existing calls directly; can also just end here.
        plan = await self.plan_step()
        if plan.stop:
            self.stopped_reason = plan.reason
            return True
        if plan.skip_llm:
            self.tool_calls = list(plan.tool_calls)
        else:
            if not await self.stream_llm():
                return False
            if self.arm_reply_rescue():
                return True    # Silent step doesn't land an empty assistant turn; the next step resends with a nudge
            self.absorb_reply_rescue()
            reply = await self.build_reply()
            if reply is not None:
                self.session.append(reply)

        # The whole list can be rewritten before dispatch (insert / remove /
        # reorder); the granularity is the list, not a single call. Skipped when
        # there are no calls: injecting calls out of thin air is plan_step's job
        # (it dispatches them directly this step), whereas this method's job is
        # "adjust the batch the model asked for". Keeping the two separate also
        # avoids an infinite-loop trap — unconditionally injecting calls every
        # step would make should_continue always true.
        if self.tool_calls:
            prepared = await self.prepare_dispatch(self.tool_calls)
            if prepared is not None:
                self.tool_calls = list(prepared)
            # Persist before dispatching, then record the dispatch: if the process
            # dies during tool execution, resuming after restart relies on two
            # things — the trailing assistant.tool_calls already being in history,
            # and the store having a record of this batch.
            await self.before_dispatch()
            self.record_dispatches()

        # Announce the whole batch of calls first (arguments are already
        # complete), then execute them one by one: the client gets the entire
        # "about to execute" batch at once, rather than only learning about call N
        # once we reach it.
        for index, call in enumerate(self.tool_calls):
            await self.emit(_ev.ToolCallReady(index, call.function.name, call.function.arguments))
        for index, call in enumerate(self.tool_calls):
            if self.run.stop_requested:
                return False
            if not await self.handle_tool_call(index, call):
                return True                # Suspended: this run ends here, waiting to be resumed
        # Loop interval point: after this step's tools finish, before the next
        # model call — deliver="step" side messages are delivered here. Only done
        # on tool steps: a plain-text step is already a conclusion point, handled
        # by should_continue's pending_input instead (delivering there is what
        # actually resumes the loop; delivering here would land the message in
        # history with no next step to act on it).
        if self.tool_calls:
            self.append_arrivals(await self.pending_step_input())
        # on_step_end runs every step (a plain-text step has also "ended" and
        # still needs persistence and notification); the StepCompleted **event**
        # is what's tied to tools specifically — its meaning is "a batch of tools
        # just finished".
        await self.on_step_end()
        if self.tool_calls:
            await self.emit(_ev.StepCompleted(self.step))
        return True

    async def stream_llm(self) -> bool:
        """Call the LLM and consume the stream. Returns ``False`` to signal a
        mid-step interruption.

        For a failure after the stream has already started producing output
        (e.g. litellm's ``MidStreamFallbackError``): back off per the
        ``runtime.llm_stream_retry`` policy and **resend the whole step** as the
        same request — at this point the step hasn't been written back to the
        session yet, so resending is safe. Any half-finished content already
        delivered downstream is reset via the ``LLMStreamRetrying`` event plus
        :meth:`on_llm_stream_retry`. With no policy configured (``None``), this
        preserves the old behavior of raising immediately on failure.
        ``CancelledError`` is always re-raised as-is and never retried.

        **A single attempt's stream consumption lives in its own
        :meth:`consume_stream` layer**: what the product layer has historically
        needed to change is just "how a chunk gets consumed" (cancellation-check
        timing, event rewriting), while the backoff/resend orchestration should
        exist in exactly one place — overriding all of stream_llm would silently
        swallow the retry logic too.
        """
        request = await self.build_request()
        policy = self.runtime.llm_stream_retry
        attempt = 0
        while True:
            attempt += 1
            acc = StreamAccumulator()
            self.stream_acc = acc
            try:
                if not await self.consume_stream(request, acc):
                    return False               # Stop consuming upstream and release the connection as early as possible
            except asyncio.CancelledError:
                raise
            except Exception as e:                  # noqa: BLE001 —— retriable errors back off and resend the whole step
                if (
                    policy is None
                    or attempt >= policy.max_attempts
                    or not policy.retriable(e)
                ):
                    raise
                backoff = policy.backoffs[min(attempt - 1, len(policy.backoffs) - 1)]
                logger.warning(
                    "llm stream failed mid-way, retrying attempt=%d/%d backoff=%.1fs "
                    "partial_len=%d exc=%s: %s",
                    attempt, policy.max_attempts, backoff, len(acc.text),
                    type(e).__name__, str(e)[:200],
                )
                await self.emit(_ev.LLMStreamRetrying(
                    attempt=attempt,
                    max_attempts=policy.max_attempts,
                    backoff_s=backoff,
                    partial_len=len(acc.text),
                    exc_type=type(e).__name__,
                ))
                await self.on_llm_stream_retry(attempt, e, acc.text)
                await asyncio.sleep(backoff)
                if self.run is not None and self.run.stop_requested:  # pyright: ignore[reportUnnecessaryComparison] —— self.run's type is never None; kept defensively
                    return False
                continue
            self.assistant_text = acc.text
            self.reasoning_text = acc.reasoning
            self.tool_calls = acc.tool_calls()
            await self.on_stream_end()
            return True

    async def consume_stream(self, request: Dict[str, Any], acc: StreamAccumulator) -> bool:
        """One attempt: send the request, consume the whole stream, feed chunks
        into ``acc``.

        Returns ``False`` for a mid-step interruption (stream_llm wraps up
        immediately on this, without retrying). Exceptions propagate as usual to
        stream_llm's retry orchestration. The product layer overrides this to
        adjust consumption details (cancellation-check timing, event rewriting);
        the retry loop itself still belongs to the framework.
        """
        stream = await self.runtime.llm.acompletion(**request)  # pyright: ignore[reportOptionalMemberAccess] —— a null llm here is a product-layer config error; let the caller observe it
        async for chunk in stream:
            await self.on_chunk(chunk)
            if self.run.stop_requested:        # Consume before checking: don't lose usage / finish_reason carried on the last chunk
                return False
            for event in acc.feed(chunk):
                event = await self.on_event(event)
                if event is not None:
                    await self.emit(event)
        return True

    async def on_llm_stream_retry(
        self, attempt: int, exc: BaseException, partial_text: str,
    ) -> None:
        """The stream failed mid-way and the whole step is about to be resent
        (no-op by default).

        The product layer resets its half-finished downstream state here:
        accumulated content, a TTS feed, this step's usage / finish_reason — the
        resent stream will produce this step's content again from scratch. The
        client-side reset is delivered via the ``LLMStreamRetrying`` event through
        the wire layer (the product layer maps that event in to_sse).
        """

    async def handle_tool_call(self, index: int, call: ToolCall) -> bool:
        """One tool call: gate -> execute (streaming) -> interaction -> shape -> write back.

        Returns ``False`` to signal that **this run has suspended** (stopped
        waiting for the user to decide); the caller should wrap up on this signal.
        ``ToolCallReady`` has already been announced for the whole batch by
        :meth:`run_step`, so it isn't sent again here.
        """
        gate = await self.before_tool(call)
        if gate.action is ToolAction.SUSPEND:
            self.suspend_marker = dict(gate.marker)
            await self.emit(_ev.Suspended(gate.marker))
            return False
        if gate.action is ToolAction.DENY:
            result = gate.result
            outcome_ok = not _result_has_error(result)
        else:
            effective_call = gate.effective_call
            call = cast(ToolCall, effective_call) if effective_call is not None else call
            await self.emit(_ev.ToolExecuting(index))
            await self.on_tool_executing(call, index)
            outcome = await self._execute_streaming(call, index)
            result = outcome.value
            outcome_ok = outcome.ok

            # The tool may not have produced a final result at all, but instead
            # "needs a human to decide"; the product layer can swap it for a plain
            # result in on_interaction_request (e.g. deciding to wait in place
            # instead) — doing so means we don't suspend.
            if isinstance(result, InteractionRequest):
                result = await self.on_interaction_request(call, index, result)
            if isinstance(result, InteractionRequest):
                return await self.suspend_for(call, index, result)
            interaction = await self.after_execute(call, result, index)
            if interaction.kind is InteractionKind.SUSPEND:
                self.suspend_marker = dict(interaction.marker)
                tcid = str(self.suspend_marker.get("tool_call_id") or getattr(call, "id", "") or "")
                if tcid:
                    self.session.truncate_tool_calls_after(tcid)
                await self.emit(_ev.Suspended(self.suspend_marker))
                return False
            if interaction.kind is InteractionKind.WAIT:
                result = await self._await_interaction(interaction, result)
            elif interaction.result is not UNSET:
                result = interaction.result

        result = await self.after_tool(call, result)

        message = await self.build_tool_message(call, result)
        if message is not None:
            self.session.append(message)
        ok = outcome_ok and not _result_has_error(result)
        await self.emit(_ev.ToolResult(index, call.function.name, result, ok=ok))
        await self.on_tool_result(call, result, index)
        return True

    async def _execute_streaming(self, call: ToolCall, index: int) -> ToolOutcome:
        """Execute one call; any deltas the tool pushes through ``stream_sink``
        during execution are forwarded one by one as ``ToolResultDelta``.

        Execution runs in its own task while the main coroutine watches a queue
        and forwards from it: whether a tool streams is entirely up to the tool
        itself (no deltas pushed means only the final result), so the loop
        doesn't need to distinguish the two kinds of tools. A tool raising an
        exception doesn't blow up the whole run — it's folded into
        ``{"error": …}`` and handed back to the model.
        """
        queue: "asyncio.Queue[object]" = asyncio.Queue()
        end = object()

        async def _run() -> ToolOutcome:
            try:
                result = await self.execute_tool(call, stream_sink=queue.put_nowait)
                if isinstance(result, ToolOutcome):
                    return result
                return ToolOutcome(result, ok=not _result_has_error(result))
            finally:
                queue.put_nowait(end)

        task = asyncio.create_task(_run())
        while True:
            delta = await queue.get()
            if delta is end:
                break
            await self.emit(_ev.ToolResultDelta(index, cast(Dict[str, Any], delta)))
        try:
            return await task
        except Exception as e:                      # noqa: BLE001 —— a tool failure shouldn't blow up the whole run
            logger.exception("tool %s failed", call.function.name)
            return ToolOutcome({"error": str(e)}, ok=False)

    async def on_interaction_request(self, call: Any, index: int, req: InteractionRequest) -> Any:
        """A tool wants to ask a human, right before suspending. Override point:
        returning it unchanged suspends; returning anything else is treated as
        this call's result and execution continues (e.g. the product layer
        decides this kind of question shouldn't suspend but should wait in place
        instead)."""
        return req

    async def _await_interaction(self, interaction: Interaction, result: Any) -> Any:
        """Wait in place for the user's response: events produced in the meantime
        are still emitted (heartbeat frames), and the result is replaced once it
        arrives."""
        resolved = result
        if interaction.waiter is None:
            return resolved  # Theoretically unreachable: callers only enter here when kind is WAIT, and WAIT always carries a waiter
        async for event, value in interaction.waiter:
            if event is not None:
                await self.emit(event)
            if value is not UNSET:
                resolved = value
        return resolved

    # ── Override points ───────────────────────────────────────────────────────

    async def plan_step(self) -> StepPlan:
        """How this step starts: call the LLM (the normal case) / dispatch
        existing calls directly / just end here.

        "Dispatch directly" covers two scenarios, whose common trait is **there's
        no need for the model to speak this step**:

        * **Resume** — a previous run already sent tool_calls out (process
          restart, suspend/resume) and we need to keep dispatching them; calling
          the LLM again would make the model re-request the same batch of tools.
        * **Bridging** — the product injects a call directly on the agent's
          behalf (e.g. relaying a question thrown back by a sub-agent straight to
          the user).

        A third scenario is "resuming a turn": this run is the user's response to
        a suspended interaction (``Contributor.ANSWER``); the first step injects
        the response as a tool result (:meth:`apply_answer`), then lets the model
        continue with that in view. If there's no pending interaction to apply
        against, we conclude immediately without calling the model.
        """
        if self.step == 0 and self.query is not None and self.query.by is Contributor.ANSWER:
            if not await self.apply_answer():
                return StepPlan.finish("no_pending_interaction")
        resumed = self.resume_dispatch_plan()
        if resumed is not None:
            return resumed
        return StepPlan.call_llm()

    def resume_dispatch_plan(self, messages: Optional[List[Dict[str, Any]]] = None) -> Optional[StepPlan]:
        """Resume dispatch after a restart: a previous process sent tool_calls out
        and didn't collect all the results -> dispatch that same batch again this
        step, without asking the model.

        Both conditions must hold: the trailing history entry is an assistant
        message with ``tool_calls``, and the store still has a dispatch record for
        this run (written by :meth:`record_dispatches`, cleared by
        :meth:`settle_dispatches`). On a match, the records are attached to
        :attr:`resuming` keyed by tool_call_id, and the executor retrieves the
        original dispatch facts (remote task IDs etc.) via
        ``ToolContext.resume_of`` to reuse rather than resend. Only checked on the
        first step; ``messages`` can supply an alternate view of history (defaults
        to the session history). Returns None on no match.

        This must be checked after ANSWER-resume: the tail of a suspended run is
        also a dangling assistant.tool_calls, and if its dispatch record is still
        present, handling it as a dispatch-resume first would re-dispatch the
        "ask a human" call all over again.
        """
        if self.step != 0:
            return None
        store = self.runtime.run_store
        reader = getattr(store, "pending_dispatches", None)
        if store is None or reader is None:
            return None
        history = messages if messages is not None else self.messages
        tail = history[-1] if history else None
        if not isinstance(tail, dict) or tail.get("role") != "assistant":
            return None
        raw_calls = tail.get("tool_calls")
        if not isinstance(raw_calls, list) or not raw_calls:
            return None
        try:
            records: Dict[str, Dict[str, Any]] = dict(reader(self.run.id) or {})
        except Exception:
            logger.warning("pending_dispatches failed run=%s (no resume)", self.run.id, exc_info=True)
            return None
        if not records:
            return None
        calls = [tool_call_from_dict(cast(Dict[str, Any], tc)) for tc in cast(List[Any], raw_calls) if isinstance(tc, dict)]
        if not calls:
            return None
        self.resuming = {
            str(getattr(c, "id", "") or ""): dict(records.get(str(getattr(c, "id", "") or ""), {}))
            for c in calls
        }
        logger.info("resume dispatch: run=%s re-dispatching %d call(s) without LLM", self.run.id, len(calls))
        return StepPlan.dispatch(calls)

    async def before_dispatch(self) -> None:
        """Persist before dispatching (default: :meth:`persist`). The assistant
        turn carrying tool_calls must be written to storage before the tools
        execute — otherwise, if the process dies during tool execution, history
        won't contain this step after a restart and dispatch-resume has nothing
        to work from. A dispatch-resume step doesn't persist again (that trailing
        entry was read from storage in the first place). Override this if the
        product layer's persistence works differently."""
        if self.resuming:
            return
        await self.persist()

    def record_dispatches(self) -> None:
        """Record this step's batch of calls as "dispatched, not yet collected"
        (when the store supports it). A dispatch-resume step doesn't re-record:
        the record is already there."""
        if self.resuming or not self.tool_calls:
            return
        recorder = getattr(self.runtime.run_store, "record_dispatch", None)
        if recorder is None:
            return
        now = time.time()
        for call in self.tool_calls:
            tcid = str(getattr(call, "id", "") or "")
            if not tcid:
                continue
            try:
                recorder(self.run.id, tcid, {"tool_name": call.function.name, "dispatched_at": now})
            except Exception:
                logger.warning("record_dispatch failed run=%s call=%s", self.run.id, tcid, exc_info=True)

    def settle_dispatches(self) -> None:
        """All of this step's tools have been collected: clear the dispatch
        records and exit the dispatch-resume state. Called by default from
        :meth:`on_step_end`; a product layer that overrides on_step_end without
        calling super must call this itself, otherwise the next restart will
        re-dispatch a batch that was already collected."""
        had_calls = bool(self.tool_calls)
        self.resuming = {}
        if not had_calls:
            return
        clearer = getattr(self.runtime.run_store, "clear_dispatches", None)
        if clearer is None:
            return
        try:
            clearer(self.run.id)
        except Exception:
            logger.warning("clear_dispatches failed run=%s", self.run.id, exc_info=True)

    async def prepare_dispatch(self, tool_calls: List[Any]) -> Optional[List[Any]]:
        """Rewrite the whole call list before dispatch (insert / remove /
        reorder). Returning ``None`` means dispatch as-is.

        The granularity is the **list**, not a single call, because the real-world
        need is whole-batch operations like "insert one in front of this batch" or
        "reorder by dependency" — e.g. when the model requests a tool from a
        package that hasn't been opened yet, insert an "open this package" call in
        front of it first. ``before_tool``'s single-call granularity can't express
        that.
        """
        return None

    async def on_stream_end(self) -> None:
        """One LLM stream has ended, **the assistant message hasn't been
        assembled yet**.

        This step's usage accounting, timing stats, and any call metadata that
        needs to be attached to the message are computed right here — they depend
        on things accumulated during the stream (usage, finish_reason), yet must
        be ready before the message is assembled.
        """
        return None

    async def suspend_for(self, call: Any, index: int, req: InteractionRequest) -> bool:
        """A tool wants to ask a human: record the marker, truncate the tail,
        emit "please respond" and Suspended, and suspend this run (returns False
        for :meth:`handle_tool_call` to return directly; a product layer that
        overrides handle_tool_call calls this at the same spot)."""
        tcid = str(getattr(call, "id", "") or "")
        marker = self.interaction_marker(call, index, req)
        self.suspend_marker = marker
        if tcid:
            cut_at = self.session.truncate_tool_calls_after(tcid)
            if cut_at is not None:
                await self.persist_message(cut_at)
        self.runtime.mark_session_suspended(self.session, marker)
        await self.on_interaction_requested(req, call, index)
        await self.emit(_ev.InteractionRequested(
            kind=req.kind, index=index, tool_name=call.function.name, tool_call_id=tcid, payload=dict(req.payload),
        ))
        await self.emit(_ev.Suspended(marker))
        return False

    def interaction_marker(self, call: Any, index: int, req: InteractionRequest) -> Dict[str, Any]:
        """The contents of the suspend marker (the pending interaction stored in
        session meta). Override point: the product layer can record extra fields."""
        return {
            "kind": req.kind,
            "tool_call_id": str(getattr(call, "id", "") or ""),
            "tool_name": call.function.name,
            "index": index,
            "payload": dict(req.payload),
            "run_id": self.run.id,       # Ownership: the guard that clears markers when other runs conclude uses this to decide "that run is still alive" and leaves it alone
            "created_at": datetime.now().isoformat(),
        }

    async def on_interaction_requested(self, req: InteractionRequest, call: Any, index: int) -> None:
        """A tool wants to ask a human (right before suspending). Override point:
        push a notification, log it out-of-band... does nothing by default."""
        return None

    async def resolve_interaction(self, pending: Dict[str, Any], query: Query) -> Any:
        """Translate the user's response into the **tool result** for the call
        that suspended. Override point.

        ``pending`` is the session's pending interaction (recorded by
        ``interaction_marker``), and ``query`` is this run's ``Contributor.ANSWER``
        contribution. Returning None means "can't be resolved yet" (the response
        is missing) -> conclude this run without calling the model. Default:
        pass ``query.content`` through as-is as the answers. The product layer
        dispatches on ``pending["kind"]``: how a Q&A / confirmation / authorization
        each turns into a result (a confirmed authorization might even need to
        actually execute that call).
        """
        if query.content is None or (isinstance(query.content, str) and not query.content.strip()):
            return None
        return {"status": "answered", "answers": query.content}

    async def apply_answer(self) -> bool:
        """Resuming a turn: inject the ``Contributor.ANSWER`` contribution as the
        tool result for the suspended call, clear the suspend marker, persist, and
        emit :class:`InteractionResolved`. Returns whether it was injected (False
        = no pending interaction to apply against)."""
        query = self.query
        if query is None or query.by is not Contributor.ANSWER:
            return False
        pending = self.session.pending_interaction
        if not pending:
            return False
        tcid = str(pending.get("tool_call_id") or "")
        if not tcid or self.session.has_tool_result(tcid):
            return False                                   # Already answered (idempotent)
        result = await self.resolve_interaction(pending, query)
        if result is None:
            return False
        tool_name = str(pending.get("tool_name") or "")
        if not self.session.has_tool_call(tcid):
            # The suspended call isn't in the current history (rewritten later /
            # lost to decryption): patch in a synthetic assistant call so the tool
            # result right after it stays a valid pair — otherwise a dangling tool
            # result gets dropped and the model never sees the answer.
            self.session.append({
                "role": "assistant", "content": None,
                "tool_calls": [{"id": tcid, "type": "function", "function": {"name": tool_name, "arguments": "{}"}}],
            })
        self.session.append({
            "role": "tool", "tool_call_id": tcid,
            "content": json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result,
        })
        self.session.clear_pending_interaction()
        self.runtime.clear_session_suspended(
            self.session.session_id, owner_id=self.session.owner_id, reason="answered",
        )
        await self.persist()
        await self.emit(_ev.InteractionResolved(
            kind=str(pending.get("kind") or ""), tool_name=tool_name, tool_call_id=tcid, result=result,
        ))
        return True

    async def after_execute(self, call: Any, result: Any, index: int) -> Interaction:
        """The tool has finished, but the result may not be final — decide
        whether to stop and wait for a human to decide.

        Continues by default. The product layer expresses "suspend" or "wait in
        place" here; see :class:`Interaction`.
        """
        return Interaction.proceed()

    async def on_tool_executing(self, call: Any, index: int) -> None:
        """A tool is about to execute — the product layer emits its own start
        frame here."""
        return None

    async def on_tool_result(self, call: Any, result: Any, index: int) -> None:
        """A tool result has landed — the product layer emits its own end frame /
        side-effect frame here (e.g. a UI layout change)."""
        return None

    async def on_loop_start(self) -> None:
        """This run is starting (before the first step) — the product layer
        emits its entry frame here."""
        return None

    async def on_loop_end(self) -> None:
        """This run is concluding normally, right before the final
        ``LoopFinished`` frame — the product layer emits its wrap-up frame here
        (e.g. a usage summary). Only called when ``should_continue`` decides to
        conclude; suspending / early exit on interruption doesn't go through this."""
        return None

    async def on_step_end(self) -> None:
        """All of this step's tools have finished running.

        **The base class already has an implementation**: persist + notify of
        history change + clear dispatch records. If a product layer overrides
        this, remember to ``await super()`` or these three things get lost —
        or, more safely, just override the two smaller methods below instead.
        """
        await self.persist()
        await self.notify_history_changed()
        self.settle_dispatches()

    async def persist_message(self, index: int) -> None:
        """A single entry in the middle of history was changed in place (e.g.
        suspend truncation) -> rewrite just that entry. ``persist``'s diff sync
        only looks at the count and the last entry; a mid-history edit must be
        persisted on the spot by whoever made the edit."""
        database = self.runtime.database
        replace = getattr(database, "replace_message", None)   # An optional method in the protocol
        if database is None or replace is None or not (0 <= index < len(self.session.messages)):
            return
        try:
            await asyncio.to_thread(
                replace, self.session.session_id, index, self.session.messages[index],
                owner_id=self.session.owner_id, keys=self.keys,
            )
        except Exception:
            logger.exception("persist_message failed session=%s index=%s", self.session.session_id, index)

    async def persist(self) -> None:
        """Sync the session to the database. **Writes only the diff** (append
        a few entries, write only those).

        Why this is a framework default rather than something the product opts
        into: a run that takes ten steps and crashes on the eighth shouldn't lose
        the first seven — that's a correctness baseline for a service-grade agent,
        not an optional feature.
        """
        database = self.runtime.database
        if database is None:
            return
        try:
            # Persisting can be heavy work on an encrypted backend: the framework
            # takes care of offloading it to a thread pool so it doesn't block the
            # event loop.
            await asyncio.to_thread(sync_session, database, self.session, keys=self.keys)
        except Exception:
            logger.exception("persist failed session=%s", self.session.session_id)

    async def notify_history_changed(self) -> None:
        """Tell the client "history changed, revision N".

        The event stream describes what the agent did, which isn't the same as
        what history currently looks like: mid-run injected meta messages,
        context compaction, and tool results written back are all invisible in
        the event stream. Without this signal, a reconnecting client has no way
        to know whether it has fallen out of sync.
        """
        self.revision += 1
        await self.emit(_ev.HistoryChanged(self.revision, message_count=len(self.session)))

    async def pending_input(self) -> List[Dict[str, Any]]:
        """Has any new input arrived at this conclusion point (deliver="turn"
        side messages plus any "step" ones that never got delivered)?

        Typical sources: the user sent another message while the agent was
        working, a background task finished, a webhook arrived. Polls
        ``runtime.inbox`` by default (see :mod:`flops_agent.seams.inbox`); the
        product layer can inject its own Inbox implementation (or override this
        method) to get "don't end, keep going" behavior for free, with no need to
        implement resumption itself.
        """
        return list(await self.runtime.inbox.poll(self.session, "turn") or [])

    async def pending_step_input(self) -> List[Dict[str, Any]]:
        """Has a side message arrived at the most recent loop interval
        (deliver="step")?

        Polled by ``run_step`` after this step's tools have all finished, before
        the next model call; the returned messages are **appended as-is** into
        the session (never synthesizing an assistant turn — history is already
        complete at both boundary types), and the model sees them on its next
        step. Polls ``runtime.inbox`` by default.
        """
        return list(await self.runtime.inbox.poll(self.session, "step") or [])

    def append_arrivals(self, arrived: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Land the arrived side messages into history as-is (filling in a
        message id, without fabricating any other turn)."""
        for message in arrived or []:
            message.setdefault(self.session.id_field, f"msg_{uuid.uuid4().hex[:12]}")
            self.session.append(message)
        return arrived or []

    async def on_cancelled(self) -> None:
        """Cleanup after being interrupted — add an interruption marker, persist.
        Called before the termination event is emitted."""
        return None

    async def on_run_end(self, status: RunStatus) -> None:
        """After this run has **fully ended** (``run.finish`` has recorded the
        final status, subscribers have been woken, session markers have been
        cleared) — release resources that belong only to this run: in-process key
        registrations, per-run client handles, etc.
        Also called when the task is cancelled (before re-raising
        CancelledError); not called if ``finish`` itself gets cancelled.
        Division of labor with :meth:`finalize`: finalize runs **before** finish
        (can still persist, still emit frames); this hook runs **after** finish
        (nothing can be emitted anymore — release only)."""
        return None

    def describe_error(self, exc: BaseException) -> str:
        """Translate this run's failure into **user-facing text** (the message
        on the ``Error`` event). Override point.

        Defaults to the raw exception text. Products typically classify this by
        provider / model into a readable message (quota exhausted, model not
        found, context too long...) — don't hand the user a raw stack trace or
        internal identifier. This only changes the message text, not the final
        status. Called **before** ``finalize`` (inside ``drive``'s except block),
        so the product layer can stash the message for finalize to use (e.g. a
        failure push notification).
        """
        return str(exc) or type(exc).__name__

    async def accept_query(self) -> None:
        """Land this turn's contribution into history. An empty Query (or no
        Query at all) means continuing from the current state."""
        if self.query is None or self.query.is_empty:
            return
        if self.query.by is Contributor.ANSWER:
            return              # A response isn't a plain message: plan_step turns it into the tool result for the suspended call
        self.session.append({"role": "user", "content": self.query.content})

    async def project_messages(self) -> List[Dict[str, Any]]:
        """History -> the messages to actually send this time (**projected at
        read time**). Identity by default: history is the wire representation.

        Why this is its own layer: **the history that gets persisted and the
        messages that get sent are not the same thing.** In a long conversation,
        an early stretch may already have been summarized into one digest, older
        tool results should be trimmed by age/tier, only the most recent few
        images should be kept, and reasoning content needs to be rewritten into
        each provider's wire format. All of this is computed "at read time" and
        shouldn't be written back into history — history is the record of fact,
        the projection is what we're actually going to say this time.

        **Called every time a request is assembled**, so it must be a pure
        function: the same session always projects to the same result, safe to
        recompute freely. The cost is one computation per step — measured at
        roughly tens of milliseconds for a 2000-message session, negligible next
        to a single LLM round trip — in exchange for **the projection always
        staying in sync with history**. The alternative, incrementally
        maintaining a parallel list, requires remembering to append to it
        wherever history is appended to, and missing one spot causes silent
        drift (real incident: resuming a turn injected a tool result into history
        without syncing the projection, leaving that call dangling on the wire,
        and the model mistakenly concluded it had been "cancelled").

        Product layers override this to plug in their own compaction / trimming
        implementation; remember to offload heavy work to ``asyncio.to_thread``.
        """
        return self.messages

    async def build_request(self) -> Dict[str, Any]:
        """History + tools + identity -> LLM request parameters.

        This method is only concerned with **assembling the request**: which
        fields exist, whose priority wins, where system goes. The messages
        actually sent are computed fresh by :meth:`project_messages` (history is
        not the wire representation); how the system prompt's **content** is
        organized lives in :meth:`build_system_prompt`. The Runner **reads** from
        the Agent (persona / recall / tools / model) and writes it into the
        request; the Agent has no idea what the request looks like.
        """
        messages = await self.project_messages()
        request: Dict[str, Any] = {"messages": messages, "stream": True}
        tools = self.visible_tools()
        if tools:
            request["tools"] = tools
        if self.runtime.model:
            request["model"] = self.runtime.model
        request.update(self.runtime.completion_kwargs)
        agent = self.agent
        if agent is not None:
            # Identity is written last so it can override the runtime's default
            # model; the system message goes at the very front.
            agent_soul = await agent.persona(self.session, query=self.query)
            agent_memory = await agent.recall(self.session, query=self.query)
            system = self.build_system_prompt(agent_soul, agent_memory)
            if system.strip():
                request["messages"] = _with_system(messages, system)
            if agent.model:
                request["model"] = agent.model
        # The silent-reply rescue injection is the very last step — it must land
        # after identity is applied, otherwise reordering messages would push the
        # nudge out.
        return self.apply_reply_rescue(request)

    def available_capabilities(self) -> Set[str]:
        """The capability tags the product layer currently has (override point,
        empty set by default). Matched against a package/tool's ``requires`` and
        ``hidden_when`` in the registry to decide which tools are visible this
        step: e.g. give ``"executor:online"`` (and its capability bits) when the
        execution backend is online, or ``"model:vision"`` when the model can see
        images itself. The tag vocabulary is defined by the product layer; the
        framework only does set operations on it."""
        return set()

    def visible_tools(self) -> List[Dict[str, Any]]:
        """The tools the model can see this step (litellm format). Override
        point; default = root navigation + tools from the session's currently
        opened packages, filtered by the registry per capability tags
        (:meth:`ToolRegistry.visible_tools`). A product layer that wants to
        project a particular tool (trim fields by capability) can override this:
        call ``super()`` first, then adjust."""
        domains = ["/tools"] + self.session.effective_packages
        return self.runtime.registry.visible_tools(domains, self.available_capabilities())

    def build_system_prompt(self, agent_soul: str, agent_memory: str) -> str:
        """Organize the identity text into the system message's **content**.
        Override point.

        "How the system message is organized" is a different layer from
        :meth:`build_request`'s "how the request is assembled", hence its own
        method. Each section goes into a list first and they're joined at the
        end; subclasses can add sections, drop sections, or change the joiner.
        """
        system_prompt: List[str] = []

        # Persona: from Agent.persona (defaults to instructions), e.g. "You are a
        # meticulous assistant...". A product layer whose persona varies by
        # session overrides Agent.persona() rather than changing this. Empty
        # strings are skipped.
        if agent_soul.strip():
            system_prompt.append(agent_soul.strip())

        # Memory: from Agent.recall (the Memory seam), e.g. "The user is named
        # Xiao Ming and prefers Python...". Placed after the persona — the other
        # way around, a persona written afterward would read like it's correcting
        # the memory just read. A recall failure already degrades to an empty
        # string.
        if agent_memory.strip():
            system_prompt.append(agent_memory.strip())

        # Sections products commonly add (override this method and append at the
        # right spot): platform rules (static constants), a tool briefing
        # (derived from visible_tools), runtime facts (date/device/open packages,
        # which change every step and generally go last). The joiner can also be
        # changed here; if you do, update any byte-locked golden tests to match.
        return "\n\n".join(system_prompt)

    async def on_chunk(self, chunk: StreamChunk) -> None:
        """Observe a typed provider chunk, including terminal token telemetry."""
        if isinstance(chunk, FinishStreamChunk):
            if chunk.usage is not None:
                self.usage = chunk.usage
            if chunk.reason is not None:
                self.finish_reason = chunk.reason

    async def on_event(self, event: Any) -> Any:
        """A parsed event is about to be delivered. Return ``None`` to swallow
        it, or return a different event to substitute it."""
        return event

    async def build_reply(self) -> Optional[Dict[str, Any]]:
        """Stream ends -> assemble the assistant message. Returning ``None``
        means this step doesn't land an assistant turn.

        **This is where continuing a reply is distinguished from starting a new
        one**: if the trailing entry is an "unfinished assistant reply", we
        should keep writing into it instead of adding a new one — see
        :attr:`Session.ends_with_open_reply`.
        """
        if not self.assistant_text and not self.reasoning_text and not self.tool_calls:
            return None
        if self.session.ends_with_open_reply and not self.tool_calls:
            tail = self.session.last
            if tail is None:
                return None  # Theoretically unreachable: messages is never empty when ends_with_open_reply is true
            tail["content"] = (tail.get("content") or "") + (self.assistant_text or "")
            if self.reasoning_text:
                tail["reasoning_content"] = self.reasoning_text
            return None                              # Continue writing in place, don't add a new message
        message: Dict[str, Any] = {"role": "assistant", "content": self.assistant_text or ""}
        if self.reasoning_text:
            message["reasoning_content"] = self.reasoning_text
        if self.tool_calls:
            message["tool_calls"] = [tool_call_to_openai(tc) for tc in self.tool_calls]
        message.setdefault(self.session.id_field, f"msg_{uuid.uuid4().hex[:12]}")
        return message

    # ── Silent-reply rescue: a concluding step with reasoning but no reply ────
    #
    # Models with an exposed reasoning channel (the DeepSeek / Qwen / Kimi
    # family) have a known failure mode: they write the entire reply into the
    # reasoning channel and stop normally without emitting a single content
    # token. What gets persisted is an assistant turn with empty content and no
    # tool_calls — from the user's point of view, the agent "only thought and
    # never spoke". Industry consensus is to treat this as a transient glitch
    # and retry once with a nudge (prefill continuation / a trailing system
    # hint), reserving "just promote the reasoning to content" as a last
    # resort. The framework implements the former here — three methods plus one
    # capability hook. A product layer that overrides build_request should
    # remember to run its own exit path through apply_reply_rescue.

    def reply_rescue_plan(self) -> Optional[Dict[str, Any]]:
        """Which channel to use to rescue a silent step. Returning ``None`` falls
        back to the trailing system nudge (works with any OpenAI-compatible
        provider). Providers that support prefill can return a plan dict to use
        the continuation channel instead, which has a higher success rate:

        * ``{"mode": "reasoning_prefill", "flag": {...}}`` — put the original
          reasoning back as-is, append a cue sentence at the end, and let the
          model continue straight into content following its own reasoning
          (works for vLLM / self-hosted setups etc. that can prefill the
          reasoning channel).
        * ``{"mode": "content_prefill", "flag": {...}}`` — prefill content with an
          opening (defaults to "Sure, "), forcing the model into the answering
          phase to continue writing (DeepSeek's beta chat prefix completion,
          dashscope's partial mode, etc.).

        ``flag`` is the provider-private marker meaning "this assistant message
        is a prefix, please continue it"; it's merged as-is into that message
        (``{"prefix": True}`` for DeepSeek, ``{"partial": True}`` for dashscope).
        Optional ``prefix_text`` / ``cue`` override the default text; optional
        ``overrides`` are merged into the request kwargs for the rescue step as a
        whole (e.g. DeepSeek prefix completion needs the beta endpoint:
        ``{"overrides": {"api_base": "https://api.deepseek.com/beta"}}``).

        A prefill-channel rescue step **doesn't include tools** by default
        (override with ``drop_tools=True``): the rescue step's only purpose is to
        get the content said, and DeepSeek's prefix mode is mutually exclusive
        with function calling (even having tool_calls show up in wire history
        gets a 400 — a capability declaration must inspect its own wire history;
        a conversation containing a tool turn should return None and fall back to
        the system nudge).

        A product layer that picks the channel per model overrides this method;
        the default reads the static ``runtime.reply_rescue_plan`` config. The
        very last resort when even the nudge fails to bring back a reply
        (promoting reasoning to content) lives in :meth:`absorb_reply_rescue`,
        not in channel selection.
        """
        return self.runtime.reply_rescue_plan

    def arm_reply_rescue(self) -> bool:
        """The stream just ended: is this step "reasoning with no reply"? If so,
        arm the rescue and don't land a turn this step.

        Checks three conditions: no tool_calls, empty content, non-empty
        reasoning. On a match, this empty assistant turn **doesn't go into
        history** (no zombie empty turns), and the loop appends another step to
        resend directly. A **totally empty response** (no reasoning either) isn't
        handled by this mechanism — that's a provider failure, injecting a nudge
        can't rescue content that was never produced, and it's left to the
        product layer's error handling.

        The attempt cap ``runtime.silent_reply_max_rescues`` (default 1) guards
        against an infinite loop; ``runtime.rescue_silent_reply=False`` disables
        this entirely.
        """
        if not self.runtime.rescue_silent_reply:
            return False
        if self.reply_rescue_attempts >= self.runtime.silent_reply_max_rescues:
            return False
        if self.tool_calls or (self.assistant_text or "").strip():
            return False
        if not (self.reasoning_text or "").strip():
            return False
        plan = dict(self.reply_rescue_plan() or {})
        self.reply_rescue_attempts += 1
        _mode = plan.get("mode") or "system_nudge"
        self.reply_rescue = {
            "reasoning": self.reasoning_text,
            "mode": _mode,
            "drop_tools": bool(plan.get(
                "drop_tools", _mode in ("content_prefill", "reasoning_prefill")
            )),
            "flag": dict(plan.get("flag") or {}),
            "prefix_text": plan.get("prefix_text") or SILENT_REPLY_CONTENT_PREFIX,
            "cue": plan.get("cue") or SILENT_REPLY_REASONING_CUE,
            "overrides": dict(plan.get("overrides") or {}),
            "applied": False,
        }
        logger.info(
            "silent reply: rescue armed run=%s step=%s mode=%s attempt=%d",
            self.run.id, self.step, self.reply_rescue["mode"], self.reply_rescue_attempts,
        )
        return True

    def apply_reply_rescue(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Inject the in-flight rescue into this LLM request (wire-only, never
        written into history).

        The last step of the framework's ``build_request``; a product layer that
        overrides build_request should also run its own exit path (after wire
        shaping) through this method.
        """
        rescue = self.reply_rescue
        if rescue is None:
            return request
        rescue["applied"] = True
        messages = list(request.get("messages") or [])
        mode = rescue["mode"]
        if mode == "reasoning_prefill":
            prefix: Dict[str, Any] = {
                "role": "assistant",
                "content": "",
                "reasoning_content": rescue["reasoning"].rstrip() + "\n\n" + rescue["cue"],
            }
            prefix.update(rescue["flag"])
            messages.append(prefix)
        elif mode == "content_prefill":
            prefix = {"role": "assistant", "content": rescue["prefix_text"]}
            prefix.update(rescue["flag"])
            messages.append(prefix)
        else:
            messages.append({
                "role": "system",
                "content": SILENT_REPLY_NUDGE_TEMPLATE.format(
                    reasoning=rescue["reasoning"].rstrip()
                ),
            })
        request = dict(request)
        request["messages"] = messages
        if rescue["drop_tools"]:
            request.pop("tools", None)
        if rescue["overrides"]:
            request.update(rescue["overrides"])
        return request

    def absorb_reply_rescue(self) -> None:
        """The rescue step's stream has ended: merge the rescued original
        reasoning back into this step's output, then land the turn normally.

        * The original reasoning goes first — it's the actual thought process
          behind this reply and shouldn't be lost just because a rescue happened;
        * The ``content_prefill`` prefix is glued back onto the content (the
          provider only returns the continuation part);
        * It's also fine for the rescue step to turn around and call a tool;
          reasoning is merged in and things proceed as normal;
        * **The rescue step still produces only reasoning** — even the nudge
          couldn't pull it back (observed in production: v4-flash ignores the
          system nudge and keeps writing everything into the reasoning channel on
          the second attempt too). In that case, **promote the latest reasoning
          segment to content**: under this failure mode, that reasoning is what
          the model actually wanted to say to the user, and surfacing it beats
          silence. This is the last resort; the model's reasoning behavior is
          never interfered with throughout, and the original reasoning stays in
          the reasoning channel.
        """
        rescue, self.reply_rescue = self.reply_rescue, None
        if rescue is None or not rescue.get("applied"):
            return
        if rescue["mode"] == "content_prefill" and (self.assistant_text or "").strip():
            self.assistant_text = rescue["prefix_text"] + self.assistant_text
        new_reasoning = self.reasoning_text if (self.reasoning_text or "").strip() else ""
        if new_reasoning and not self.tool_calls and not (self.assistant_text or "").strip():
            # Promote reasoning to content: content takes the rescue step's
            # reasoning (which, from the user's perspective, is the reply
            # itself); the reasoning channel keeps the original reasoning
            # rescued at arm time, so the two don't duplicate each other.
            self.assistant_text = new_reasoning.strip()
            self.reasoning_text = rescue["reasoning"].rstrip()
            logger.info(
                "silent reply: rescue nudge ignored, promoted reasoning to content "
                "run=%s step=%s", self.run.id, self.step,
            )
            return
        parts = [rescue["reasoning"].rstrip()]
        if rescue["mode"] == "reasoning_prefill":
            parts.append(rescue["cue"])
        if new_reasoning:
            parts.append(new_reasoning)
        self.reasoning_text = "\n\n".join(parts)

    async def before_tool(self, call: ToolCall) -> ToolGate:
        """The gate before tool execution (:class:`ToolGate`): allow / rewrite the
        call / deny with a substitute result / suspend this run.

        The product layer does security review, argument correction, and
        permission gating here. Allows by default.
        """
        return ToolGate.proceed()

    async def execute_tool(self, call: ToolCall, *, stream_sink: Any = None) -> object:
        """Actually execute one tool call. ``stream_sink`` is the callback the
        tool uses to push deltas (the framework turns these into
        ``ToolResultDelta``)."""
        return await self.runtime.executor.execute(call, self.tool_context(call, stream_sink))

    def tool_context(self, call: ToolCall, stream_sink: Any = None) -> ToolContext:
        """Build the context handed to the executor. The product layer can add
        its own dimensions here."""
        tcid = call.id or None
        return ToolContext(
            user_id=self.session.owner_id,
            conversation_id=self.session.session_id,
            function_name=call.function.name,
            tool_domains=["/tools"] + self.session.effective_packages,
            stream_sink=stream_sink,
            registry=self.runtime.registry,
            session=self.session,
            runtime=self.runtime,
            run_id=self.run.id,
            tool_call_id=tcid,
            resume_of=self.resuming.get(tcid) if tcid else None,
        )

    async def after_tool(self, call: ToolCall, result: object) -> object:
        """Shape the result."""
        return result

    async def build_tool_message(self, call: ToolCall, result: object) -> Optional[Dict[str, Any]]:
        """Tool result -> the message written back into history."""
        return {
            "role": "tool",
            "tool_call_id": call.id,
            "content": result_to_content(result),
            self.session.id_field: f"msg_{uuid.uuid4().hex[:12]}",
        }

    async def should_continue(self) -> bool:
        """Should we take another step?

        Two situations continue: this step called a tool (the classic agent
        loop), **or new input arrived at the turn boundary**. The latter is a
        necessary behavior for a service-grade agent — if the user sends another
        message while the agent is working and we don't act on it right away,
        they'd have to wait for this run to finish and send it again manually,
        which is a clear step down in experience.
        """
        if self.tool_calls:
            return True
        arrived = await self.pending_input()
        if not arrived:
            return False
        self.append_arrivals(arrived)
        await self.persist()
        await self.notify_history_changed()
        return True

    @property
    def agent(self) -> Optional["Agent"]:
        """This run's identity. Session-level takes priority over runtime-level —
        a single process can serve multiple personas."""
        return getattr(self.session, "agent", None) or self.runtime.agent

    async def finalize(self, status: RunStatus) -> None:
        """Wrap-up. The base class persists by default — whether the run ended
        normally, was interrupted, or failed, progress is never lost.

        Also schedules a memory update afterward. **Not awaited**: that's often
        several LLM calls, and waiting on it would mean the subscriber's stream
        takes several extra seconds to close — the user has already finished
        reading the reply while the UI is still spinning.
        """
        await self.persist()
        agent = self.agent
        if agent is not None and status is RunStatus.DONE:
            # Only update memory on a normal conclusion: a run that was
            # interrupted or failed has incomplete content, and distilling it
            # would write half-finished facts into long-term memory.
            agent.schedule_remember(self.session)

    @override
    def __repr__(self) -> str:
        return f"<{type(self).__name__} run={self.run.id} step={self.step}>"


__all__ = ["Runner"]
