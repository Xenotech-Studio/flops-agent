"""Framework Inbox — the two delivery timings for out-of-turn input, and the
guarantee that "delivery never fabricates a turn".

Background (a real incident, fixed 2026-07-23): a product-side homegrown
continuation loop consumed the "deliver once settled" queue at the **end of a
tool step**, and to make room for it, appended an empty assistant turn —
content='' with no tool_calls — which got persisted as a permanently
"corrupted message". Now that the mechanism lives in the framework, three
things are nailed down here:

1. ``deliver="step"``: delivered after this step's tools finish running, before
   the next model call (the nearest loop boundary);
2. ``deliver="turn"``: delivered once this round of work settles (the model no
   longer wants tools), continuing as a new user turn; a turn-level message is
   **never** delivered at the end of a tool step;
3. Both delivery modes are plain appends — an assistant turn with "neither
   content nor tool_calls" must never appear in history (the zombie regression
   guard).

Run: ``python3 backend/tests/test_kernel_inbox.py`` (or via pytest).
"""
import asyncio
import os
import sys
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent import Runtime, MemoryInbox, Session  # noqa: E402


def _chunk(content=None, tool_call=None, finish=None):
    delta = SimpleNamespace(content=content, reasoning_content=None, tool_calls=None)
    if tool_call is not None:
        idx, name, args = tool_call
        delta.tool_calls = [SimpleNamespace(
            index=idx, id=f"call_{name}_{idx}",
            function=SimpleNamespace(name=name, arguments=args),
        )]
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)],
                           usage=None)


class ScriptedLLM:
    """Each acompletion call emits one step from the script: ("tool", name, args) or ("text", content)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.seen_messages = []          # snapshot (shallow copy) of the messages received at each step

    async def acompletion(self, **kw):
        self.seen_messages.append([dict(m) for m in kw.get("messages", [])])
        step = self.script.pop(0)
        self.calls += 1

        async def _stream():
            if step[0] == "tool":
                yield _chunk(tool_call=(0, step[1], step[2]))
                yield _chunk(finish="tool_calls")
            else:
                yield _chunk(content=step[1])
                yield _chunk(finish="stop")

        return _stream()


def _drive(runtime, session, inject_after_start=None):
    """Drives one run to completion. inject_after_start(runtime) fires right
    after the run starts (simulating "the user sends another message while
    the agent is running" — queuing before the first LLM step is enough to
    hit the first boundary)."""
    async def _go():
        run = runtime.start(session)
        if inject_after_start is not None:
            inject_after_start(runtime)
        async for _ in run:
            pass
        return run.status
    return asyncio.run(_go())


def _no_zombie(session):
    for m in session.messages:
        if m.get("role") != "assistant":
            continue
        has_content = isinstance(m.get("content"), str) and m["content"].strip() != ""
        has_tc = bool(m.get("tool_calls"))
        assert has_content or has_tc, f"a zombie assistant turn snuck into history: {m!r}"


def test_step_delivery_lands_at_tool_boundary():
    """deliver="step": the interjection lands after the tool result, before the next LLM request — the model sees it on its very next step."""
    llm = ScriptedLLM([
        ("tool", "probe", "{}"),
        ("text", "Got the interjection, handled it along with everything else"),
    ])
    runtime = Runtime(llm=llm, tools=[{
        "schema": {"type": "function", "function": {"name": "probe", "parameters": {}}},
        "fn": lambda **kw: {"ok": True},
    }])
    session = Session("s_step", messages=[{"role": "user", "content": "do the work"}])
    status = _drive(
        runtime, session,
        inject_after_start=lambda rt: rt.deliver(
            "s_step", {"role": "user", "content": "Also: take a look at this too"}, when="step"),
    )
    assert str(status.value if hasattr(status, "value") else status).lower().endswith("done")
    roles = [(m["role"], bool(m.get("tool_calls"))) for m in session.messages]
    # user -> assistant(tool) -> tool -> user(interjection) -> assistant(text)
    assert roles == [("user", False), ("assistant", True), ("tool", False),
                     ("user", False), ("assistant", False)], roles
    # The interjection is already visible in the second-step LLM request
    assert any(m.get("content") == "Also: take a look at this too" for m in llm.seen_messages[1])
    _no_zombie(session)
    print("test_step_delivery_lands_at_tool_boundary OK")


def test_turn_delivery_waits_for_turn_end():
    """deliver="turn": not delivered at the end of a tool step; delivered as a new turn continuing the same run once the model settles (no longer wants tools)."""
    llm = ScriptedLLM([
        ("tool", "probe", "{}"),
        ("text", "First thing is done"),
        ("text", "Replied about the second thing too"),
    ])
    runtime = Runtime(llm=llm, tools=[{
        "schema": {"type": "function", "function": {"name": "probe", "parameters": {}}},
        "fn": lambda **kw: {"ok": True},
    }])
    session = Session("s_turn", messages=[{"role": "user", "content": "Do the first thing first"}])
    _drive(
        runtime, session,
        inject_after_start=lambda rt: rt.deliver(
            "s_turn", {"role": "user", "content": "Once that's done, look at the second thing"}, when="turn"),
    )
    assert llm.calls == 3                      # continues at the settle point, one more step in the same run
    # Not visible yet at the end of the tool step (second-step request); visible once settled (third-step request)
    assert not any(m.get("content") == "Once that's done, look at the second thing" for m in llm.seen_messages[1])
    assert any(m.get("content") == "Once that's done, look at the second thing" for m in llm.seen_messages[2])
    # Delivery point: right after the settling reply turn (history stays complete, no fabricated turn needed or allowed)
    idx_reply = next(i for i, m in enumerate(session.messages)
                     if m["role"] == "assistant" and m.get("content") == "First thing is done")
    idx_inject = next(i for i, m in enumerate(session.messages)
                      if m.get("content") == "Once that's done, look at the second thing")
    assert idx_inject == idx_reply + 1
    _no_zombie(session)
    print("test_turn_delivery_waits_for_turn_end OK")


def test_step_message_not_stranded_when_no_more_tool_steps():
    """Fallback for "find the nearest boundary": when the model settles
    directly with no further tool steps, the step message is delivered at
    the settle point (which is itself a boundary), and must never be
    stranded past the end of the run."""
    llm = ScriptedLLM([
        ("text", "Answered directly"),
        ("text", "Added a reply to the interjection"),
    ])
    runtime = Runtime(llm=llm, tools=[])
    session = Session("s_fallback", messages=[{"role": "user", "content": "I have a question"}])
    _drive(
        runtime, session,
        inject_after_start=lambda rt: rt.deliver(
            "s_fallback", {"role": "user", "content": "One more thing"}, when="step"),
    )
    assert llm.calls == 2
    assert any(m.get("content") == "One more thing" for m in session.messages)
    _no_zombie(session)
    print("test_step_message_not_stranded_when_no_more_tool_steps OK")


def test_memory_inbox_fifo_and_isolation():
    """MemoryInbox: session isolation + FIFO within a queue + poll drains what it returns."""
    inbox = MemoryInbox()
    inbox.push("a", {"role": "user", "content": "1"})
    inbox.push("a", {"role": "user", "content": "2"})
    inbox.push("b", {"role": "user", "content": "x"}, deliver="step")
    s_a = SimpleNamespace(session_id="a")
    s_b = SimpleNamespace(session_id="b")
    assert asyncio.run(inbox.poll(s_a, "step")) == []          # turn-level messages are not delivered at a step boundary
    got = asyncio.run(inbox.poll(s_a, "turn"))
    assert [m["content"] for m in got] == ["1", "2"]
    assert asyncio.run(inbox.poll(s_a, "turn")) == []          # empty once drained
    assert [m["content"] for m in asyncio.run(inbox.poll(s_b, "step"))] == ["x"]
    try:
        inbox.push("a", {"role": "user", "content": "?"}, deliver="later")
        raise AssertionError("an invalid deliver value should raise")
    except ValueError:
        pass
    print("test_memory_inbox_fifo_and_isolation OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in _TESTS:
        t()
    print(f"\nALL {len(_TESTS)} TESTS PASSED")
