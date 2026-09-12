"""Human-in-the-loop belongs to the framework: a tool needs to ask a human -> suspend; the user's
reply starts the next turn as an ANSWER contribution -> the framework injects it as a tool result ->
the model keeps going."""
import asyncio
import json
import os
import sys
from types import SimpleNamespace as SN

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent import (  # noqa: E402
    Contributor, InMemoryDatabase, Query, Runner, Runtime, ToolRegistry, register_ask_user_question,
)
from flops_agent.engine.execution import RunStatus  # noqa: E402
from flops_agent.engine.interaction import InteractionRequest  # noqa: E402
from flops_agent.entities import events as ev  # noqa: E402


def _tool(name):
    return {"type": "function", "function": {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}}}


def _chunk(content=None, calls=None):
    d = {"content": content, "reasoning_content": None, "tool_calls": None}
    if calls:
        d["tool_calls"] = [SN(index=i, id=cid, type="function", function=SN(name=n, arguments=json.dumps(a))) for i, (cid, n, a) in enumerate(calls)]
    return SN(choices=[SN(delta=SN(**d), finish_reason=None, index=0)], usage=None)


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


def _registry():
    reg = ToolRegistry()
    register_ask_user_question(reg)
    async def ping():
        return {"ok": True}
    reg.register_tool("/tools", "ping", _tool("ping"), ping)
    async def deploy(arguments, ctx):
        return InteractionRequest(kind="confirm", payload={"what": "rm -rf build/"})
    reg.register_tool("/tools", "deploy", _tool("deploy"))
    reg.register_unified_handler("/tools", "deploy", deploy)
    return reg


QUESTION = {"questions": [{"question": "Continue?", "options": ["yes", "no"]}]}


def test_ask_suspends_and_answer_resumes():
    async def go():
        db = InMemoryDatabase()
        llm = ScriptedLLM(
            [_chunk(calls=[("c1", "ask_user_question", QUESTION), ("c2", "ping", {})])],   # second call never gets to run -> trimmed off on suspend
            [_chunk(content="OK, continuing.")],
        )
        rt = Runtime(llm=llm, registry=_registry(), database=db)
        s = await rt.load_session("s1", owner_id="u1")
        run = rt.start(s, Query(content="help me deploy", by=Contributor.USER))
        events = [d.event async for d in run.subscribe()]
        kinds = [type(e).__name__ for e in events]
        assert run.status is RunStatus.SUSPENDED, kinds
        assert kinds.index("InteractionRequested") < kinds.index("Suspended")
        req = next(e for e in events if isinstance(e, ev.InteractionRequested))
        assert req.kind == "ask_user_question" and req.tool_call_id == "c1" and req.payload["questions"][0]["question"] == "Continue?"
        pending = s.pending_interaction
        assert pending and pending["tool_call_id"] == "c1" and pending["kind"] == "ask_user_question"
        assert (db.load_meta("s1", owner_id="u1") or {}).get(s.suspended_field), "suspend marker has been persisted"
        last_assistant = [m for m in s.messages if m.get("role") == "assistant"][-1]
        assert [tc["id"] for tc in last_assistant["tool_calls"]] == ["c1"], "tool_calls after the suspending call are trimmed off"
        assert not any(m.get("role") == "tool" for m in s.messages), "no tool result is persisted while suspended"

        # -- user reply: an ANSWER contribution starts the next turn --
        s2 = await rt.load_session("s1", owner_id="u1")
        run2 = rt.start(s2, Query(content=[{"question": "Continue?", "answer": "yes"}], by=Contributor.ANSWER))
        events2 = [d.event async for d in run2.subscribe()]
        kinds2 = [type(e).__name__ for e in events2]
        assert run2.status is RunStatus.DONE, kinds2
        resolved = next(e for e in events2 if isinstance(e, ev.InteractionResolved))
        assert resolved.tool_call_id == "c1" and resolved.result["answers"][0]["answer"] == "yes"
        tool_msg = next(m for m in s2.messages if m.get("role") == "tool")
        assert tool_msg["tool_call_id"] == "c1" and "yes" in tool_msg["content"]
        assert not any(m.get("role") == "user" and m.get("content") and "answer" in json.dumps(m.get("content")) for m in s2.messages), "the reply is not a user message"
        assert s2.pending_interaction is None and not (db.load_meta("s1", owner_id="u1") or {}).get(s2.suspended_field)
        # history the model sees at the resumed step: assistant(tool_call c1) -> tool(answer)
        seen = llm.seen[-1]
        assert seen[-1]["role"] == "tool" and seen[-1]["tool_call_id"] == "c1"
        assert seen[-2]["role"] == "assistant" and seen[-2]["tool_calls"][0]["id"] == "c1"
    asyncio.run(go())
    print("test_ask_suspends_and_answer_resumes OK")


def test_answer_without_pending_or_twice_is_a_noop():
    async def go():
        llm = ScriptedLLM([_chunk(content="never")])
        rt = Runtime(llm=llm, registry=_registry(), database=InMemoryDatabase())
        s = await rt.load_session("s1", owner_id="u1")
        run = rt.start(s, Query(content="yes", by=Contributor.ANSWER))
        events = [d.event async for d in run.subscribe()]
        fin = next(e for e in events if isinstance(e, ev.LoopFinished))
        assert fin.reason == "no_pending_interaction" and llm.seen == [], "no pending interaction: finish right away without calling the model"
        assert s.messages == [], "the reply is not persisted to history"
    asyncio.run(go())
    print("test_answer_without_pending_or_twice_is_a_noop OK")


def test_host_resolves_other_interaction_kinds():
    """A confirm-style interaction: the host translates "approve" into actually executing it and
    "reject" into a rejection result inside resolve_interaction."""
    async def go():
        class Host(Runner):
            async def resolve_interaction(self, pending, query):
                if pending["kind"] == "confirm":
                    if query.content == "approve":
                        return {"success": True, "executed": pending["payload"]["what"]}
                    if query.content == "reject":
                        return {"success": False, "error": "user rejected"}
                    return None
                return await super().resolve_interaction(pending, query)

        llm = ScriptedLLM([_chunk(calls=[("d1", "deploy", {})])], [_chunk(content="done")])
        rt = Runtime(llm=llm, registry=_registry(), database=InMemoryDatabase(), runner=Host)
        s = await rt.load_session("s1", owner_id="u1")
        run = rt.start(s, Query(content="deploy", by=Contributor.USER))
        async for _ in run.subscribe():
            pass
        assert run.status is RunStatus.SUSPENDED and s.pending_interaction["kind"] == "confirm"
        # missing reply (None) -> finish without calling the model; then reply approve -> injected and resumed
        run2 = rt.start(s, Query(content="", by=Contributor.ANSWER))
        async for _ in run2.subscribe():
            pass
        assert run2.status is RunStatus.DONE and s.pending_interaction is not None, "still undecided: the marker is kept"
        run3 = rt.start(s, Query(content="approve", by=Contributor.ANSWER))
        async for _ in run3.subscribe():
            pass
        tool_msg = next(m for m in s.messages if m.get("role") == "tool")
        assert json.loads(tool_msg["content"])["executed"] == "rm -rf build/" and s.pending_interaction is None
    asyncio.run(go())
    print("test_host_resolves_other_interaction_kinds OK")


def test_suspend_persists_truncation_and_answer_rebuilds_missing_call():
    """The suspend-time truncation must be persisted (a mid-history edit is invisible to the persist
    diff sync, so suspend_for rewrites that single record); when resuming, if the suspending call is
    no longer in history (rewritten later / lost on decrypt), synthesize an assistant call so the
    tool result still has a valid pair."""
    async def go():
        db = InMemoryDatabase()
        llm = ScriptedLLM(
            [_chunk(calls=[("c1", "ask_user_question", QUESTION), ("c2", "ping", {})])],
            [_chunk(content="Continuing.")],
        )
        rt = Runtime(llm=llm, registry=_registry(), database=db)
        s = await rt.load_session("s1", owner_id="u1")
        run = rt.start(s, Query(content="ask me", by=Contributor.USER))
        async for _ in run.subscribe():
            pass
        stored = db.load_messages("s1", owner_id="u1")
        stored_assistant = [m for m in stored if m.get("role") == "assistant"][-1]
        assert [tc["id"] for tc in stored_assistant["tool_calls"]] == ["c1"], "the truncated assistant message has been persisted"
        # the suspending call has disappeared from history (simulating a rewrite)
        s.messages[:] = [m for m in s.messages if m.get("role") != "assistant"]
        run2 = rt.start(s, Query(content=[{"answer": "yes"}], by=Contributor.ANSWER))
        events = [d.event async for d in run2.subscribe()]
        assert run2.status is RunStatus.DONE
        roles = [m.get("role") for m in s.messages]
        i = roles.index("tool")
        assert s.messages[i - 1]["role"] == "assistant" and s.messages[i - 1]["tool_calls"][0]["id"] == "c1", "a synthetic call was added"
        resolved = next(e for e in events if isinstance(e, ev.InteractionResolved))
        assert resolved.tool_call_id == "c1" and resolved.result["answers"] == [{"answer": "yes"}]
        wire = llm.seen[-1]
        assert any(m.get("role") == "tool" and m.get("tool_call_id") == "c1" for m in wire), "the model saw the answer"
    asyncio.run(go())
    print("test_suspend_persists_truncation_and_answer_rebuilds_missing_call OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in _TESTS:
        t()
    print(f"\nALL {len(_TESTS)} INTERACTION TESTS PASSED")
