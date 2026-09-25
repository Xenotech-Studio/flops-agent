"""Unit tests for the Runner extension points -- whether the five gaps were actually closed.

These five are the things that didn't fit anywhere when "claiming" the 33
existing hooks one by one (see the extension-point inventory). Once filled in, it must be proven that a
product can express the original behavior through them, rather than working
around the gap with a hack.

1. A step that doesn't call the LLM at all (resume / bridging)
2. Before the stream ends and the message is built (usage accounting)
3. Rewriting the whole call list before dispatch (silently inserting a prerequisite tool)
4. Stopping after execution to wait for a human decision (suspend / wait-in-place)
5. Emitting product-custom frames at lifecycle moments
"""
import asyncio
import os
import sys
from types import SimpleNamespace as SN

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent import Query, RunStatus, Runner, Runtime, Session  # noqa: E402
from flops_agent.entities import events as ev  # noqa: E402
from flops_agent.engine.interaction import UNSET, Interaction, StepPlan  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def chunk(**delta):
    d = {"content": None, "reasoning_content": None, "tool_calls": None, **delta}
    return SN(choices=[SN(delta=SN(**d), finish_reason=None)], usage=None)


def tool_chunk(name, args="{}", index=0, cid="c1"):
    return chunk(tool_calls=[SN(index=index, id=cid, type="function",
                                function=SN(name=name, arguments=args))])


def call_obj(name, args="{}", cid="c1"):
    return SN(id=cid, type="function", function=SN(name=name, arguments=args))


class FakeLLM:
    def __init__(self, *steps):
        self.steps, self.calls, self.last_request = list(steps), 0, None

    async def acompletion(self, **kw):
        self.last_request = kw
        step = self.steps[self.calls] if self.calls < len(self.steps) else []
        self.calls += 1

        async def gen():
            for c in step:
                yield c
        return gen()


def ping():
    """A tool."""
    return {"ok": True}


def opener():
    """A prerequisite tool."""
    return {"opened": True}


# ── Gap 1: a step that doesn't call the LLM ──────────────────────────────────

def test_plan_step_can_dispatch_without_llm():
    """Resume / bridging: a call already issued in a previous turn must be dispatched again without asking the model."""
    class ResumeRunner(Runner):
        async def plan_step(self):
            if self.step == 0:
                return StepPlan.dispatch([call_obj("ping")])
            return StepPlan.call_llm()

    async def go():
        llm = FakeLLM([chunk(content="got it")])
        runtime = Runtime(llm=llm, tools=[ping], runner=ResumeRunner)
        run = runtime.start(Session("s1"), Query.text("q"))
        events = [d.event async for d in run]
        results = [e for e in events if isinstance(e, ev.ToolResult)]
        assert results and results[0].result == {"ok": True}
        assert llm.calls == 1              # step 0 didn't call the LLM, only step 1 did
    _run(go())
    print("test_plan_step_can_dispatch_without_llm OK")


def test_plan_step_can_finish_immediately():
    async def go():
        class StopRunner(Runner):
            async def plan_step(self):
                return StepPlan.finish("nothing_to_do")

        llm = FakeLLM([chunk(content="never")])
        run = Runtime(llm=llm, runner=StopRunner).start(Session("s1"), Query.text("q"))
        events = [d.event async for d in run]
        assert llm.calls == 0
        finished = [e for e in events if isinstance(e, ev.LoopFinished)]
        assert finished and finished[0].reason == "nothing_to_do"
    _run(go())
    print("test_plan_step_can_finish_immediately OK")


# ── Gap 2: before the stream ends and the message is built ──────────────────

def test_on_stream_end_runs_before_reply_is_built():
    """Usage accounting depends on what's accumulated over the stream, yet must run before the message is built."""
    order = []

    class AccountingRunner(Runner):
        async def on_stream_end(self):
            order.append(("stream_end", self.assistant_text))

        async def build_reply(self):
            order.append(("build_reply", self.assistant_text))
            return await super().build_reply()

    async def go():
        runtime = Runtime(llm=FakeLLM([chunk(content="hi")]), runner=AccountingRunner)
        await Runtime.start(runtime, Session("s1"), Query.text("q")).wait()
        assert order == [("stream_end", "hi"), ("build_reply", "hi")]
    _run(go())
    print("test_on_stream_end_runs_before_reply_is_built OK")


def test_on_stream_open_runs_after_request_before_first_chunk():
    """流已建立钩子夹在 acompletion 与首 chunk 之间，产品无需覆写 consume_stream。"""
    order = []

    class OpeningRunner(Runner):
        async def on_stream_open(self):
            order.append("open")

        async def on_chunk(self, raw_chunk):
            order.append("chunk")
            await super().on_chunk(raw_chunk)

    async def go():
        runtime = Runtime(llm=FakeLLM([chunk(content="hi")]), runner=OpeningRunner)
        await runtime.start(Session("s1"), Query.text("q")).wait()
        assert order == ["open", "chunk"]

    _run(go())
    print("test_on_stream_open_runs_after_request_before_first_chunk OK")


def test_before_llm_call_runs_before_acompletion_and_on_stream_open():
    """before_llm_call 必须先于 acompletion 调用发生——它是产品层起「等待模型」指示器
    最早也最可靠的挂钩点：acompletion 经常要等供应商响应就绪才返回，返回后到首个
    chunk 之间可能只有几毫秒，on_stream_open 那时才起指示器往往已经来不及。"""
    order = []

    class BeforeCallRunner(Runner):
        async def before_llm_call(self):
            order.append("before_call")

        async def on_stream_open(self):
            order.append("open")

        async def on_chunk(self, raw_chunk):
            order.append("chunk")
            await super().on_chunk(raw_chunk)

    async def go():
        runtime = Runtime(llm=FakeLLM([chunk(content="hi")]), runner=BeforeCallRunner)
        await runtime.start(Session("s1"), Query.text("q")).wait()
        assert order == ["before_call", "open", "chunk"]

    _run(go())
    print("test_before_llm_call_runs_before_acompletion_and_on_stream_open OK")


# ── Gap 3: rewriting the whole list before dispatch ──────────────────────────

def test_prepare_dispatch_can_insert_prerequisite_call():
    """The model requested a tool that hasn't been unpacked yet -> insert an "open the toolkit" call for it first."""
    class InjectingRunner(Runner):
        async def prepare_dispatch(self, tool_calls):
            # Realistic shape: insert only when the prerequisite is missing, not unconditionally on every step.
            if any(c.function.name == "opener" for c in tool_calls):
                return None
            return [call_obj("opener", cid="auto")] + list(tool_calls)

    async def go():
        runtime = Runtime(
            llm=FakeLLM([tool_chunk("ping")], [chunk(content="done")]),
            tools=[ping, opener], runner=InjectingRunner,
        )
        run = runtime.start(Session("s1"), Query.text("q"))
        events = [d.event async for d in run]
        names = [e.name for e in events if isinstance(e, ev.ToolResult)]
        assert names == ["opener", "ping"]        # the prerequisite tool was inserted first
    _run(go())
    print("test_prepare_dispatch_can_insert_prerequisite_call OK")


def test_prepare_dispatch_skipped_when_no_tool_calls():
    """It isn't called when the model didn't request a tool -- "injecting out of nowhere" is plan_step's job.

    Otherwise an unconditional-injection implementation would make
    should_continue always true and loop forever.
    """
    called = []

    class Watcher(Runner):
        async def prepare_dispatch(self, tool_calls):
            called.append(len(tool_calls))
            return None

    async def go():
        runtime = Runtime(llm=FakeLLM([chunk(content="plain text")]), runner=Watcher)
        await runtime.start(Session("s1"), Query.text("q")).wait()
        assert called == []
    _run(go())
    print("test_prepare_dispatch_skipped_when_no_tool_calls OK")


def test_prepare_dispatch_none_keeps_original():
    async def go():
        runtime = Runtime(llm=FakeLLM([tool_chunk("ping")], [chunk(content="x")]),
                          tools=[ping])
        run = runtime.start(Session("s1"), Query.text("q"))
        events = [d.event async for d in run]
        assert [e.name for e in events if isinstance(e, ev.ToolResult)] == ["ping"]
    _run(go())
    print("test_prepare_dispatch_none_keeps_original OK")


# ── Gap 4: stopping after execution to wait for a human decision ────────────

def test_after_execute_can_suspend_the_run():
    """Dangerous command / multiple-choice: this turn stops here and waits for the user to respond in another request."""
    class SuspendingRunner(Runner):
        async def after_execute(self, call, result, index):
            return Interaction.suspend(kind="confirm", tool=call.function.name)

    async def go():
        runtime = Runtime(llm=FakeLLM([tool_chunk("ping")], [chunk(content="never")]),
                          tools=[ping], runner=SuspendingRunner)
        run = runtime.start(Session("s1"), Query.text("q"))
        events = [d.event async for d in run]
        assert await run.wait() is RunStatus.SUSPENDED
        suspended = [e for e in events if isinstance(e, ev.Suspended)]
        assert suspended and suspended[0].marker["kind"] == "confirm"
        assert not any(isinstance(e, ev.ToolResult) for e in events)   # the result never got finalized
    _run(go())
    print("test_after_execute_can_suspend_the_run OK")


def test_after_execute_can_wait_in_place():
    """A second-scale decision: the connection stays open, heartbeat frames keep going out, and it continues once answered."""
    class WaitingRunner(Runner):
        async def after_execute(self, call, result, index):
            async def waiter():
                yield ev.ProductEvent("keepalive", {"type": "keepalive"}), UNSET
                yield None, {"answered": True}
            return Interaction.wait_for(waiter())

    async def go():
        runtime = Runtime(llm=FakeLLM([tool_chunk("ping")], [chunk(content="ok")]),
                          tools=[ping], runner=WaitingRunner)
        run = runtime.start(Session("s1"), Query.text("q"))
        events = [d.event async for d in run]
        assert any(isinstance(e, ev.ProductEvent) and e.kind == "keepalive" for e in events)
        result = [e for e in events if isinstance(e, ev.ToolResult)][0]
        assert result.result == {"answered": True}      # the user's response replaced the tool result
        assert await run.wait() is RunStatus.DONE
    _run(go())
    print("test_after_execute_can_wait_in_place OK")


def test_after_execute_can_replace_result():
    class ReplacingRunner(Runner):
        async def after_execute(self, call, result, index):
            return Interaction.proceed(result={"replaced": True})

    async def go():
        runtime = Runtime(llm=FakeLLM([tool_chunk("ping")], [chunk(content="x")]),
                          tools=[ping], runner=ReplacingRunner)
        run = runtime.start(Session("s1"), Query.text("q"))
        events = [d.event async for d in run]
        assert [e for e in events if isinstance(e, ev.ToolResult)][0].result == {"replaced": True}
    _run(go())
    print("test_after_execute_can_replace_result OK")


def test_suspend_stops_remaining_tool_calls():
    """A suspend must block the remaining calls in the same step -- otherwise it keeps acting before the user has decided."""
    executed = []

    class SuspendFirstRunner(Runner):
        async def execute_tool(self, call, *, stream_sink = None):
            executed.append(call.function.name)
            return await super().execute_tool(call)

        async def after_execute(self, call, result, index):
            return Interaction.suspend(at=index) if index == 0 else Interaction.proceed()

    async def go():
        two = chunk(tool_calls=[
            SN(index=0, id="c1", type="function", function=SN(name="ping", arguments="{}")),
            SN(index=1, id="c2", type="function", function=SN(name="opener", arguments="{}")),
        ])
        runtime = Runtime(llm=FakeLLM([two], [chunk(content="x")]),
                          tools=[ping, opener], runner=SuspendFirstRunner)
        run = runtime.start(Session("s1"), Query.text("q"))
        await run.wait()
        assert executed == ["ping"]                  # the second call never ran
    _run(go())
    print("test_suspend_stops_remaining_tool_calls OK")


# ── Gap 5: emitting product frames at lifecycle moments ──────────────────────

def test_lifecycle_points_fire_in_order():
    seen = []

    class TracingRunner(Runner):
        async def on_loop_start(self):
            seen.append("loop_start")

        async def on_tool_executing(self, call, index):
            seen.append("tool_executing")

        async def on_tool_result(self, call, result, index):
            seen.append("tool_result")

        async def on_step_end(self):
            seen.append("step_end")

    async def go():
        runtime = Runtime(llm=FakeLLM([tool_chunk("ping")], [chunk(content="done")]),
                          tools=[ping], runner=TracingRunner)
        await runtime.start(Session("s1"), Query.text("q")).wait()
        # step_end fires on every step (a plain-text wrap-up step still needs to
        # persist and notify), so it fires twice across the two steps; the two
        # tool-related points only show up on the first step.
        assert seen == ["loop_start", "tool_executing", "tool_result",
                        "step_end", "step_end"]
    _run(go())
    print("test_lifecycle_points_fire_in_order OK")


def test_on_cancelled_runs_before_terminal_event():
    """Cancellation cleanup (marking, persisting) must run before the terminal event is emitted."""
    order = []

    class CancelAwareRunner(Runner):
        async def on_cancelled(self):
            order.append("cancelled_hook")

        async def on_event(self, event):
            if isinstance(event, ev.Cancelled):
                order.append("cancelled_event")
            return event

    async def go():
        gate, entered = asyncio.Event(), asyncio.Event()

        class Blocking:
            async def acompletion(self, **kw):
                async def gen():
                    entered.set()
                    await gate.wait()
                    yield chunk(content="late")
                return gen()

        runtime = Runtime(llm=Blocking(), runner=CancelAwareRunner)
        run = runtime.start(Session("s1"), Query.text("q"))
        await asyncio.wait_for(entered.wait(), 1)
        await run.stop()
        gate.set()
        await asyncio.wait_for(run.wait(), 1)
        assert order and order[0] == "cancelled_hook"
    _run(go())
    print("test_on_cancelled_runs_before_terminal_event OK")


def test_product_events_reach_subscribers():
    class FramingRunner(Runner):
        async def on_loop_start(self):
            await self.emit(ev.ProductEvent("entry", {"type": "entry", "n": 1}))

    async def go():
        runtime = Runtime(llm=FakeLLM([chunk(content="x")]), runner=FramingRunner)
        run = runtime.start(Session("s1"), Query.text("q"))
        events = [d.event async for d in run]
        entry = [e for e in events if isinstance(e, ev.ProductEvent)]
        assert entry and entry[0].payload["n"] == 1
    _run(go())
    print("test_product_events_reach_subscribers OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in _TESTS:
        t()
    print(f"\nALL {len(_TESTS)} EXTENSION-POINT TESTS PASSED")
