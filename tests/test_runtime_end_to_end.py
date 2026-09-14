"""Runtime end-to-end unit tests — the contract actually running for the first time.

This suite is the **executable verification of the contract**: everything the
whitepaper describes — "the minimal three-line path", "multi-turn", "tool
calling", "interruption", "reconnection" — actually gets run here, item by
item. If it doesn't run, the design has a hole; the docs aren't just wrong.

Uses a fake LLM (replaying fixed chunks) plus plain Python functions as
tools; touches neither the network nor a database.
"""
import asyncio
import os
import sys
from types import SimpleNamespace as SN

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent import (  # noqa: E402
    ToolGate,
    Query,
    Run,
    RunStatus,
    Runner,
    Runtime,
    Session,
    SessionRunActiveError,
)
from flops_agent.entities import events as ev  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def chunk(**delta):
    d = {"content": None, "reasoning_content": None, "tool_calls": None, **delta}
    return SN(choices=[SN(delta=SN(**d), finish_reason=None)], usage=None)


def tool_chunk(index=0, cid="c1", name=None, args=None):
    return chunk(tool_calls=[SN(index=index, id=cid, type="function",
                                function=SN(name=name, arguments=args))])


class FakeLLM:
    """Replays a fixed list of chunks per step."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = 0
        self.last_request = None

    async def acompletion(self, **kw):
        self.last_request = kw
        step = self.steps[self.calls] if self.calls < len(self.steps) else []
        self.calls += 1

        async def gen():
            for c in step:
                yield c
        return gen()


def get_weather(city):
    """Look up the weather."""
    return {"city": city, "temp_c": 21}


# ── Minimal path: the whitepaper's first example ────────────────────────────

def test_minimal_path_three_lines():
    async def go():
        runtime = Runtime(llm=FakeLLM([chunk(content="Hello"), chunk(content="world")]))
        got = [e async for e in runtime.ask("You there?")]
        texts = [e.text for e in got if isinstance(e, ev.TextDelta)]
        assert texts == ["Hello", "world"]
        assert any(isinstance(e, ev.LoopFinished) for e in got)
    _run(go())
    print("test_minimal_path_three_lines OK")


def test_ask_yields_events_not_deliveries():
    """A one-shot Q&A never reconnects, so a cursor is dead weight — hand back events directly."""
    async def go():
        runtime = Runtime(llm=FakeLLM([chunk(content="hi")]))
        first = None
        async for e in runtime.ask("x"):
            first = e
            break
        assert isinstance(first, ev.TextDelta)
    _run(go())
    print("test_ask_yields_events_not_deliveries OK")


# ── Session: multi-turn, history accumulation ───────────────────────────────

def test_multi_turn_accumulates_history():
    async def go():
        runtime = Runtime(llm=FakeLLM([chunk(content="A")], [chunk(content="B")]))
        session = Session("s1")
        for text in ("First question", "Second question"):
            run = runtime.start(session, Query.text(text))
            await run.wait()
        roles = [m["role"] for m in session.messages]
        assert roles == ["user", "assistant", "user", "assistant"]
        assert session.messages[-1]["content"] == "B"
        # The second request carries the full history
        assert len(runtime.llm.last_request["messages"]) == 4
    _run(go())
    print("test_multi_turn_accumulates_history OK")


def test_start_rejects_an_active_session_run_before_creating_another():
    """start() promises an already-started Run, so it never silently queues one."""
    async def go():
        runtime = Runtime(llm=FakeLLM([chunk(content="first")]))
        session = Session("s1")
        first = runtime.start(session, Query.text("one"))
        try:
            runtime.start(session, Query.text("two"))
        except SessionRunActiveError as exc:
            assert exc.session_id == "s1" and exc.run_id == first.id
        else:
            raise AssertionError("active session must reject another start")
        await first.wait()

    _run(go())
    print("test_start_rejects_an_active_session_run_before_creating_another OK")


def test_empty_query_continues_from_state():
    """No Query = continue from the current state. If the last message is a half-finished reply, continue writing it in place rather than appending a new one."""
    async def go():
        runtime = Runtime(llm=FakeLLM([chunk(content=" second half")]))
        session = Session("s1", messages=[
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "first half"},
        ])
        run = runtime.start(session)                  # no query given
        await run.wait()
        assert len(session.messages) == 2             # no new message added
        assert session.messages[-1]["content"] == "first half second half"
    _run(go())
    print("test_empty_query_continues_from_state OK")


def test_regenerate_is_truncate_plus_run():
    """Regenerate isn't a kind of Query — it's two steps: "truncate history" then "run with no input"."""
    async def go():
        runtime = Runtime(llm=FakeLLM([chunk(content="new answer")]))
        session = Session("s1", messages=[
            {"external_id": "u1", "role": "user", "content": "q"},
            {"external_id": "a1", "role": "assistant", "content": "old answer"},
        ])
        session.truncate_after("u1")                  # step one: drop the old answer
        run = runtime.start(session)                  # step two: run with no input
        await run.wait()
        assert [m["content"] for m in session.messages] == ["q", "new answer"]
    _run(go())
    print("test_regenerate_is_truncate_plus_run OK")


# ── Tool calling ─────────────────────────────────────────────────────────────

def test_tool_call_round_trip():
    async def go():
        runtime = Runtime(
            llm=FakeLLM(
                [tool_chunk(name="get_weather", args='{"city": "Paris"}')],
                [chunk(content="Paris is 21 degrees")],
            ),
            tools=[get_weather],
        )
        session = Session("s1")
        run = runtime.start(session, Query.text("Paris weather"))
        events = [d.event async for d in run]
        results = [e for e in events if isinstance(e, ev.ToolResult)]
        assert results and results[0].result == {"city": "Paris", "temp_c": 21}
        roles = [m["role"] for m in session.messages]
        assert roles == ["user", "assistant", "tool", "assistant"]
    _run(go())
    print("test_tool_call_round_trip OK")


def test_tool_schema_derived_from_callable():
    async def go():
        runtime = Runtime(llm=FakeLLM([chunk(content="ok")]), tools=[get_weather])
        run = runtime.start(Session("s1"), Query.text("x"))
        await run.wait()
        schemas = runtime.llm.last_request["tools"]
        assert schemas[0]["function"]["name"] == "get_weather"
        assert "city" in schemas[0]["function"]["parameters"]["properties"]
    _run(go())
    print("test_tool_schema_derived_from_callable OK")


# ── Runner subclassing: the product's extension point ───────────────────────

def test_subclass_overrides_a_step():
    """The product subclasses Runner to override a step — this replaces the old "hooks + scratch" approach."""
    class MyRunner(Runner):
        async def build_request(self):
            request = await super().build_request()
            request["messages"] = [{"role": "system", "content": "You are an assistant"}] + request["messages"]
            return request

    async def go():
        runtime = Runtime(llm=FakeLLM([chunk(content="ok")]), runner=MyRunner)
        run = runtime.start(Session("s1"), Query.text("hi"))
        await run.wait()
        assert runtime.llm.last_request["messages"][0]["role"] == "system"
    _run(go())
    print("test_subclass_overrides_a_step OK")


def test_subclass_state_lives_on_self():
    """A run's intermediate state lives on the instance — no more need for a shared dict."""
    class CountingRunner(Runner):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.chunks_seen = 0          # this is what used to be a scratch key

        async def on_chunk(self, chunk):
            await super().on_chunk(chunk)
            self.chunks_seen += 1

        async def finalize(self, status):
            self.run.chunks_seen = self.chunks_seen   # piggyback on run to carry the result out
            await super().finalize(status)

    async def go():
        runtime = Runtime(
            llm=FakeLLM([chunk(content="a"), chunk(content="b"), chunk(content="c")]),
            runner=CountingRunner,
        )
        run = runtime.start(Session("s1"), Query.text("x"))
        await run.wait()
        assert run.chunks_seen == 3
    _run(go())
    print("test_subclass_state_lives_on_self OK")


def test_before_tool_can_deny():
    class GatedRunner(Runner):
        async def before_tool(self, call):
            return ToolGate.deny({"error": "denied by security policy"})

    async def go():
        runtime = Runtime(
            llm=FakeLLM([tool_chunk(name="get_weather", args='{"city":"X"}')],
                        [chunk(content="OK")]),
            tools=[get_weather], runner=GatedRunner,
        )
        run = runtime.start(Session("s1"), Query.text("q"))
        events = [d.event async for d in run]
        result = [e for e in events if isinstance(e, ev.ToolResult)][0]
        assert result.result == {"error": "denied by security policy"}
        assert not any(isinstance(e, ev.ToolExecuting) for e in events)   # never executed
    _run(go())
    print("test_before_tool_can_deny OK")


# ── Interruption and reconnection ───────────────────────────────────────────

class BlockingLLM:
    """Hangs on the first chunk until released — used to create a real "in-flight" state.

    If the fake LLM never awaits, the whole run would complete inside the
    caller's first sleep(0), leaving no window to interrupt it at all (that
    would be a test artifact, not real product behavior).
    """

    def __init__(self):
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()

    async def acompletion(self, **kw):
        async def gen():
            self.entered.set()
            await self.gate.wait()          # actually suspends here
            yield chunk(content="late")
        return gen()


def test_stop_ends_run_as_stopped():
    async def go():
        llm = BlockingLLM()
        runtime = Runtime(llm=llm)
        run = runtime.start(Session("s1"), Query.text("q"))
        await asyncio.wait_for(llm.entered.wait(), 1)   # confirm it's actually running
        assert runtime.runs.find("s1") is run
        assert await runtime.stop("s1") is True          # found by session and interrupted
        llm.gate.set()                                   # release it so it reaches the checkpoint
        assert await asyncio.wait_for(run.wait(), 1) is RunStatus.STOPPED
    _run(go())
    print("test_stop_ends_run_as_stopped OK")


def test_stop_unknown_session_returns_false():
    async def go():
        assert await Runtime(llm=FakeLLM()).stop("nope") is False
    _run(go())
    print("test_stop_unknown_session_returns_false OK")


def test_second_client_reconnects_by_cursor():
    """After a run finishes, another client can still catch up on history — proof that execution and subscription are decoupled."""
    async def go():
        runtime = Runtime(llm=FakeLLM([chunk(content="a"), chunk(content="b")]))
        run = runtime.start(Session("s1"), Query.text("q"))
        first = [d async for d in run]                  # the first client receives everything
        assert len(first) >= 2
        again = [d.event async for d in run.subscribe(first[0].cursor)]
        assert len(again) == len(first) - 1             # resumes from the cursor, no duplicates, no gaps
    _run(go())
    print("test_second_client_reconnects_by_cursor OK")


def test_run_leaves_pool_when_finished():
    async def go():
        runtime = Runtime(llm=FakeLLM([chunk(content="x")]))
        run = runtime.start(Session("s1"), Query.text("q"))
        await run.wait()
        await asyncio.sleep(0)
        assert runtime.runs.find("s1") is None          # leaves the pool as soon as it finishes
    _run(go())
    print("test_run_leaves_pool_when_finished OK")


# ── Failures and edge cases ──────────────────────────────────────────────────

def test_llm_failure_marks_run_failed():
    class BoomLLM:
        async def acompletion(self, **kw):
            raise RuntimeError("provider down")

    async def go():
        run = Runtime(llm=BoomLLM()).start(Session("s1"), Query.text("q"))
        assert await asyncio.wait_for(run.wait(), 1) is RunStatus.FAILED
        assert isinstance(run.error, RuntimeError)
    _run(go())
    print("test_llm_failure_marks_run_failed OK")


def test_tool_exception_becomes_error_result():
    def boom():
        raise ValueError("broken")

    async def go():
        runtime = Runtime(
            llm=FakeLLM([tool_chunk(name="boom", args="{}")], [chunk(content="Got it")]),
            tools=[boom],
        )
        run = runtime.start(Session("s1"), Query.text("q"))
        events = [d.event async for d in run]
        result = [e for e in events if isinstance(e, ev.ToolResult)][0]
        assert "broken" in result.result["error"]
        assert run.status is RunStatus.DONE            # a tool failure shouldn't blow up the whole run
    _run(go())
    print("test_tool_exception_becomes_error_result OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in _TESTS:
        t()
    print(f"\nALL {len(_TESTS)} RUNTIME E2E TESTS PASSED")
