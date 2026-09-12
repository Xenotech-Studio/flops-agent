"""Read-time projection override point: history != wire.

The framework defaults to identity (history is the wire); products override it to plug in
their own compression / trimming. The key property is that **every request assembly recomputes
from scratch** -- an approach that incrementally maintains a parallel wire list requires
remembering to sync at every point history is appended to, and missing one causes silent drift.
"""
import asyncio
import json
import os
import sys
from types import SimpleNamespace as SN

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent import Agent, InMemoryDatabase, Query, Runner, Runtime  # noqa: E402
from flops_agent.engine.execution import RunStatus  # noqa: E402


def _chunk(content=None):
    return SN(choices=[SN(delta=SN(content=content, reasoning_content=None, tool_calls=None),
                          finish_reason=None, index=0)], usage=None)


class ScriptedLLM:
    def __init__(self, *steps):
        self.steps, self.i, self.seen = list(steps), 0, []

    async def acompletion(self, **req):
        self.seen.append([dict(m) for m in req["messages"]])
        step = self.steps[self.i]; self.i += 1

        async def gen():
            for c in step:
                yield c
        return gen()


def test_default_projection_is_identity():
    async def go():
        llm = ScriptedLLM([_chunk("hi")])
        rt = Runtime(llm=llm, database=InMemoryDatabase())
        s = await rt.load_session("s1", owner_id="u1")
        run = rt.start(s, Query.text("are you there"))
        _ = [d async for d in run.subscribe()]
        assert run.status is RunStatus.DONE
        sent = llm.seen[0]
        assert [(m["role"], m["content"]) for m in sent] == [("user", "are you there")], sent
        assert sent[0] is not s.messages[0] or sent == s.messages, "the default projection is history itself"
    asyncio.run(go())
    print("test_default_projection_is_identity OK")


def test_projection_is_recomputed_every_request_and_never_touches_history():
    """Two steps: the projection for step two must see the assistant message step one just
    appended to history -- only recomputing on each request makes that possible.
    Meanwhile history itself is unaffected by the projection (projection is read-only, never written back)."""
    calls = {"n": 0}

    class Projecting(Runner):
        async def project_messages(self):
            calls["n"] += 1
            # A typical projection: fold the early segment into one system summary + keep the tail verbatim
            tail = self.messages[-2:]
            return [{"role": "system", "content": f"Summary so far ({len(self.messages)} messages)"}] + [
                {k: v for k, v in m.items() if k != "id"} for m in tail
            ]

        async def should_continue(self):
            return self.step == 0        # run exactly two steps

    async def go():
        llm = ScriptedLLM([_chunk("step one")], [_chunk("step two")])
        rt = Runtime(llm=llm, database=InMemoryDatabase(), runner=Projecting)
        s = await rt.load_session("s2", owner_id="u1")
        run = rt.start(s, Query.text("start"))
        _ = [d async for d in run.subscribe()]
        assert run.status is RunStatus.DONE
        assert calls["n"] == 2, "every request assembly must recompute"
        first, second = llm.seen
        assert first[0]["content"] == "Summary so far (1 messages)"
        assert [m["content"] for m in first[1:]] == ["start"]
        # Step two: step one's assistant message has already landed in history, the projection must include it
        assert second[0]["content"] == "Summary so far (2 messages)"
        assert [m["content"] for m in second[1:]] == ["start", "step one"]
        # Projection is read-only: the fabricated system summary must never land in history
        assert not any(m.get("role") == "system" for m in s.messages)
        assert [m["role"] for m in s.messages] == ["user", "assistant"]
    asyncio.run(go())
    print("test_projection_is_recomputed_every_request_and_never_touches_history OK")


def test_identity_and_persona_ride_on_top_of_the_projection():
    """The identity system message rides on top of the projection, not on top of raw history."""
    class Trimming(Runner):
        async def project_messages(self):
            return [{"role": "user", "content": "the only message after projection"}]

    class Persona(Agent):
        async def persona(self, session, *, query=None):
            return "You are an assistant"

    async def go():
        llm = ScriptedLLM([_chunk("ok")])
        rt = Runtime(llm=llm, database=InMemoryDatabase(), runner=Trimming, agent=Persona())
        s = await rt.load_session("s3", owner_id="u1")
        s.append({"role": "user", "content": "first sentence"})
        s.append({"role": "assistant", "content": "second sentence"})
        run = rt.start(s, Query.text("third sentence"))
        _ = [d async for d in run.subscribe()]
        sent = llm.seen[0]
        assert sent[0]["role"] == "system" and "You are an assistant" in sent[0]["content"]
        assert [m["content"] for m in sent[1:]] == ["the only message after projection"], "raw history should not leak into the request"
    asyncio.run(go())
    print("test_identity_and_persona_ride_on_top_of_the_projection OK")


if __name__ == "__main__":
    test_default_projection_is_identity()
    test_projection_is_recomputed_every_request_and_never_touches_history()
    test_identity_and_persona_ride_on_top_of_the_projection()
