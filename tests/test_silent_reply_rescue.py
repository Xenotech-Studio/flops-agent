"""Silent-reply rescue — a "reasoning only, no reply" terminal step gets one
guided retry.

Models with an open reasoning channel occasionally write the whole reply into
their reasoning and then stop with zero content tokens. What the framework
should do: don't persist that empty assistant step to history; issue one
guided retry (either a prefill continuation or a trailing system nudge) that
merges the original reasoning into the final reply; if the retry is still
silent, degrade gracefully — never loop forever.
"""
import asyncio
import os
import sys
from types import SimpleNamespace as SN

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent import Query, Runtime, Session  # noqa: E402
from flops_agent.engine.runner import (  # noqa: E402
    SILENT_REPLY_CONTENT_PREFIX,
    SILENT_REPLY_REASONING_CUE,
)
from flops_agent.seams.database import InMemoryDatabase  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def chunk(**delta):
    d = {"content": None, "reasoning_content": None, "tool_calls": None, **delta}
    return SN(choices=[SN(delta=SN(**d), finish_reason=None)], usage=None)


def tool_chunk(name, args="{}"):
    return chunk(tool_calls=[SN(index=0, id="c1", type="function",
                                function=SN(name=name, arguments=args))])


class FakeLLM:
    """Yields a stream per step; records every request so tests can assert on
    the shape of the injected nudge."""

    def __init__(self, *steps):
        self.steps, self.calls, self.requests = list(steps), 0, []

    async def acompletion(self, **kw):
        self.requests.append(kw)
        step = self.steps[self.calls] if self.calls < len(self.steps) else []
        self.calls += 1

        async def gen():
            for c in step:
                yield c
        return gen()


SILENT = [chunk(reasoning_content="Actually the answer is 42, I'll just tell the user.")]


def _session(db):
    db.create_session("s1")
    return Session("s1")


# ── Default channel: trailing system nudge ──────────────────────────────────

def test_silent_step_rescued_via_system_nudge():
    async def go():
        db = InMemoryDatabase()
        llm = FakeLLM(SILENT, [chunk(reasoning_content="Some extra reasoning."), chunk(content="The answer is 42.")])
        runtime = Runtime(llm=llm, database=db)
        await runtime.start(_session(db), Query.text("What is the answer")).wait()

        # The retry request: one extra trailing system nudge that carries the rescued original reasoning
        assert llm.calls == 2
        tail = llm.requests[1]["messages"][-1]
        assert tail["role"] == "system"
        assert "Actually the answer is 42" in tail["content"]

        # Only one assistant message in history: content comes from the retry, original reasoning is merged at the front
        stored = db.load_messages("s1")
        assert [m["role"] for m in stored] == ["user", "assistant"]
        assert stored[1]["content"] == "The answer is 42."
        assert stored[1]["reasoning_content"].startswith("Actually the answer is 42")
        assert "Some extra reasoning." in stored[1]["reasoning_content"]
    _run(go())
    print("test_silent_step_rescued_via_system_nudge OK")


# ── Prefill channel ──────────────────────────────────────────────────────────

def test_content_prefill_channel():
    async def go():
        db = InMemoryDatabase()
        llm = FakeLLM(SILENT, [chunk(content="The answer is 42.")])
        runtime = Runtime(
            llm=llm, database=db,
            reply_rescue_plan={"mode": "content_prefill", "flag": {"prefix": True}},
        )
        await runtime.start(_session(db), Query.text("What is the answer")).wait()

        tail = llm.requests[1]["messages"][-1]
        assert tail == {"role": "assistant",
                        "content": SILENT_REPLY_CONTENT_PREFIX, "prefix": True}

        stored = db.load_messages("s1")
        # Prefilled prefix is stitched back onto the content; original reasoning is preserved
        assert stored[1]["content"] == SILENT_REPLY_CONTENT_PREFIX + "The answer is 42."
        assert stored[1]["reasoning_content"].startswith("Actually the answer is 42")
    _run(go())
    print("test_content_prefill_channel OK")


def test_reasoning_prefill_channel():
    async def go():
        db = InMemoryDatabase()
        llm = FakeLLM(SILENT, [chunk(content="The answer is 42.")])
        runtime = Runtime(
            llm=llm, database=db,
            reply_rescue_plan={"mode": "reasoning_prefill", "flag": {"partial": True}},
        )
        await runtime.start(_session(db), Query.text("What is the answer")).wait()

        tail = llm.requests[1]["messages"][-1]
        assert tail["role"] == "assistant" and tail["partial"] is True
        assert tail["reasoning_content"].startswith("Actually the answer is 42")
        assert tail["reasoning_content"].endswith(SILENT_REPLY_REASONING_CUE)

        stored = db.load_messages("s1")
        assert stored[1]["content"] == "The answer is 42."
        # Both the original reasoning and the cue sentence stay in the final message (mirrors the wire, no hidden rewrite)
        assert SILENT_REPLY_REASONING_CUE in stored[1]["reasoning_content"]
    _run(go())
    print("test_reasoning_prefill_channel OK")


def test_prefill_rescue_drops_tools_and_merges_overrides():
    """The prefill rescue step doesn't carry tools (DeepSeek's prefix and
    function calling are mutually exclusive, and the rescue step doesn't need
    tools anyway); plan.overrides (e.g. switching to the beta endpoint) gets
    merged into the request kwargs."""
    def ping():
        return {"ok": True}

    async def go():
        db = InMemoryDatabase()
        llm = FakeLLM(SILENT, [chunk(content="The answer is 42.")])
        runtime = Runtime(
            llm=llm, database=db, tools=[ping],
            reply_rescue_plan={"mode": "content_prefill", "flag": {"prefix": True},
                               "overrides": {"api_base": "https://x/beta"}},
        )
        await runtime.start(_session(db), Query.text("What is the answer")).wait()
        assert "tools" in llm.requests[0]
        assert "tools" not in llm.requests[1]
        assert llm.requests[1]["api_base"] == "https://x/beta"
    _run(go())
    print("test_prefill_rescue_drops_tools_and_merges_overrides OK")


def test_system_nudge_keeps_tools():
    """The system-nudge fallback channel leaves tools untouched: the model is
    free to use a tool once more before answering."""
    def ping():
        return {"ok": True}

    async def go():
        db = InMemoryDatabase()
        llm = FakeLLM(SILENT, [chunk(content="The answer is 42.")])
        runtime = Runtime(llm=llm, database=db, tools=[ping])
        await runtime.start(_session(db), Query.text("What is the answer")).wait()
        assert "tools" in llm.requests[1]
    _run(go())
    print("test_system_nudge_keeps_tools OK")


# ── Edge cases: no false positives, no infinite loop, can be disabled ───────

def test_normal_replies_untouched():
    async def go():
        db = InMemoryDatabase()
        llm = FakeLLM([chunk(reasoning_content="Let me think."), chunk(content="Normal reply.")])
        runtime = Runtime(llm=llm, database=db)
        await runtime.start(_session(db), Query.text("Hi")).wait()
        assert llm.calls == 1
        stored = db.load_messages("s1")
        assert stored[1]["content"] == "Normal reply."
    _run(go())
    print("test_normal_replies_untouched OK")


def test_totally_empty_response_is_not_rescued():
    """A completely empty response (not even reasoning) is not this mechanism's target."""
    async def go():
        db = InMemoryDatabase()
        llm = FakeLLM([])
        runtime = Runtime(llm=llm, database=db)
        await runtime.start(_session(db), Query.text("Hi")).wait()
        assert llm.calls == 1                      # no retry
        stored = db.load_messages("s1")
        assert [m["role"] for m in stored] == ["user"]   # and no empty turn persisted
    _run(go())
    print("test_totally_empty_response_is_not_rescued OK")


def test_rescue_fires_once_then_promotes_reasoning():
    """If the retry is still reasoning-only (observed in practice with
    v4-flash ignoring the nudge): the latest reasoning gets promoted to
    content — it's what the model wanted to say, and surfacing it beats
    silence; the original reasoning stays on the reasoning channel, and we
    never arm a second retry."""
    async def go():
        db = InMemoryDatabase()
        llm = FakeLLM(SILENT, [chunk(reasoning_content="Still just thinking, not saying anything.")])
        runtime = Runtime(llm=llm, database=db)
        await runtime.start(_session(db), Query.text("What is the answer")).wait()
        assert llm.calls == 2
        stored = db.load_messages("s1")
        assert [m["role"] for m in stored] == ["user", "assistant"]
        assert stored[1]["content"] == "Still just thinking, not saying anything."
        assert stored[1]["reasoning_content"] == "Actually the answer is 42, I'll just tell the user."
    _run(go())
    print("test_rescue_fires_once_then_promotes_reasoning OK")


def test_rescue_step_totally_empty_degrades_quietly():
    """Rescue step comes back completely empty (not even reasoning): nothing
    to promote, so the original reasoning is persisted and content stays
    empty (falls back to a sentinel at read time); no crash, no loop."""
    async def go():
        db = InMemoryDatabase()
        llm = FakeLLM(SILENT, [])
        runtime = Runtime(llm=llm, database=db)
        await runtime.start(_session(db), Query.text("What is the answer")).wait()
        assert llm.calls == 2
        stored = db.load_messages("s1")
        assert stored[1]["content"] == ""
        assert stored[1]["reasoning_content"] == "Actually the answer is 42, I'll just tell the user."
    _run(go())
    print("test_rescue_step_totally_empty_degrades_quietly OK")


def test_rescue_step_may_switch_to_tools():
    """It's also valid for the rescue step to switch to calling a tool: the
    reasoning merges into the tool turn, and the loop continues as usual."""
    def ping():
        return {"ok": True}

    async def go():
        db = InMemoryDatabase()
        llm = FakeLLM(SILENT, [tool_chunk("ping")], [chunk(content="Done looking it up.")])
        runtime = Runtime(llm=llm, database=db, tools=[ping])
        await runtime.start(_session(db), Query.text("Look it up")).wait()
        stored = db.load_messages("s1")
        assert [m["role"] for m in stored] == ["user", "assistant", "tool", "assistant"]
        assert stored[1]["tool_calls"]
        assert stored[1]["reasoning_content"].startswith("Actually the answer is 42")
        assert stored[3]["content"] == "Done looking it up."
    _run(go())
    print("test_rescue_step_may_switch_to_tools OK")


def test_kill_switch():
    async def go():
        db = InMemoryDatabase()
        llm = FakeLLM(SILENT)
        runtime = Runtime(llm=llm, database=db, rescue_silent_reply=False)
        await runtime.start(_session(db), Query.text("Hi")).wait()
        assert llm.calls == 1
        stored = db.load_messages("s1")
        # With the switch off, behavior reverts to the old default: empty content + reasoning persisted as-is
        assert stored[1]["content"] == "" and stored[1]["reasoning_content"]
    _run(go())
    print("test_kill_switch OK")


if __name__ == "__main__":
    test_silent_step_rescued_via_system_nudge()
    test_content_prefill_channel()
    test_reasoning_prefill_channel()
    test_prefill_rescue_drops_tools_and_merges_overrides()
    test_system_nudge_keeps_tools()
    test_normal_replies_untouched()
    test_totally_empty_response_is_not_rescued()
    test_rescue_fires_once_then_promotes_reasoning()
    test_rescue_step_totally_empty_degrades_quietly()
    test_rescue_step_may_switch_to_tools()
    test_kill_switch()
    print("all silent-reply-rescue tests OK")
