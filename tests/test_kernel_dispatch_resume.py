"""Resuming a dispatch within a restarted step is the framework's job: persist to storage +
record before dispatching; if the process dies mid-tool -> the new process's first step resumes
the dispatch directly, without asking the model; the executor gets back its own recorded
dispatch facts via ctx.resume_of; once all are collected, the record is cleared."""
import asyncio
import json
import os
import sys
from types import SimpleNamespace as SN

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent import InMemoryDatabase, InMemoryRunStore, Query, Runtime, ToolRegistry  # noqa: E402
from flops_agent.engine.execution import RunStatus  # noqa: E402
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
        self.steps, self.calls = list(steps), 0

    async def acompletion(self, **req):
        step = self.steps[self.calls]
        self.calls += 1

        async def gen():
            for c in step:
                yield c
        return gen()


def _registry(handler):
    reg = ToolRegistry()
    reg.register_tool("/tools", "slow", _tool("slow"))
    reg.register_unified_handler("/tools", "slow", handler)
    return reg


def test_dispatch_is_recorded_then_resumed_without_llm():
    async def go():
        db, store = InMemoryDatabase(), InMemoryRunStore()
        started, release = asyncio.Event(), asyncio.Event()

        async def slow(arguments, ctx):
            # The executor records its own dispatch facts (remote task id, target machine) back onto the same record
            ctx.record_dispatch(task_id="task-9", device_id="dev-a")
            started.set()
            await release.wait()
            return {"ok": True}

        llm = ScriptedLLM([_chunk(calls=[("c1", "slow", {})])], [_chunk(content="done")])
        rt = Runtime(llm=llm, registry=_registry(slow), database=db, run_store=store)
        s = await rt.load_session("s1", owner_id="u1")
        run = rt.start(s, Query.text("go"))
        await asyncio.wait_for(started.wait(), 2)

        # Before dispatch: the assistant message with tool_calls has been persisted, and the
        # record contains both framework fields and fields appended by the executor
        persisted = (await rt.load_session("s1", owner_id="u1")).messages
        assert persisted[-1]["role"] == "assistant" and [tc["id"] for tc in persisted[-1]["tool_calls"]] == ["c1"]
        rec = store.pending_dispatches(run.id)
        assert rec["c1"]["tool_name"] == "slow" and rec["c1"]["task_id"] == "task-9" and rec["c1"]["device_id"] == "dev-a"
        assert "dispatched_at" in rec["c1"]

        # Process dies mid-tool: shut down with an interruption + the driving task gets collected
        await rt.shutdown()
        run.task.cancel()
        try:
            await run.task
        except asyncio.CancelledError:
            pass
        assert store.get_meta(run.id).status == "interrupted"
        assert store.pending_dispatches(run.id), "interruption must not clear the record -- it's the evidence used to resume"

        # New process: resumes with the same run_id. The first step skips the model and re-dispatches
        # c1 directly; the executor gets resume_of
        seen = []

        async def slow_again(arguments, ctx):
            seen.append(dict(ctx.resume_of or {}))
            return {"ok": True, "resumed": True}

        llm2 = ScriptedLLM([_chunk(content="done")])
        rt2 = Runtime(llm=llm2, registry=_registry(slow_again), database=db, run_store=store)
        s2 = await rt2.load_session("s1", owner_id="u1")
        run2 = rt2.start(s2, run_id=run.id)
        events = [d.event async for d in run2.subscribe()]
        assert run2.status is RunStatus.DONE, [type(e).__name__ for e in events]
        assert llm2.calls == 1, "the resume-dispatch step must not call the LLM; a normal step follows to wrap up"
        assert seen and seen[0]["task_id"] == "task-9" and seen[0]["device_id"] == "dev-a" and seen[0]["tool_name"] == "slow"
        tool_msgs = [m for m in s2.messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1 and tool_msgs[0]["tool_call_id"] == "c1" and json.loads(tool_msgs[0]["content"])["resumed"] is True
        assert sum(1 for m in s2.messages if m.get("role") == "assistant" and m.get("tool_calls")) == 1, "there should be no second tool_calls round"
        assert store.pending_dispatches(run.id) == {}, "the record is cleared once everything is collected"
        readies = [e for e in events if isinstance(e, ev.ToolCallReady)]
        assert readies and readies[-1].name == "slow"
    asyncio.run(go())
    print("test_dispatch_is_recorded_then_resumed_without_llm OK")


def test_no_records_means_no_resume_and_answer_takes_precedence():
    async def go():
        db, store = InMemoryDatabase(), InMemoryRunStore()

        async def slow(arguments, ctx):
            return {"ok": True}

        # The tail message is assistant.tool_calls but with no record (e.g. the tool already
        # finished and the history tail just happens to look this way) -> ask the model as usual
        llm = ScriptedLLM([_chunk(content="hi")])
        rt = Runtime(llm=llm, registry=_registry(slow), database=db, run_store=store)
        s = await rt.load_session("s1", owner_id="u1")
        s.append({"role": "user", "content": "x"})
        s.append({"role": "assistant", "content": "", "tool_calls": [{"id": "c9", "type": "function", "function": {"name": "slow", "arguments": "{}"}}]})
        run = rt.start(s, run_id="r-plain")
        _ = [d async for d in run.subscribe()]
        assert run.status is RunStatus.DONE and llm.calls == 1
        assert not any(m.get("role") == "tool" for m in s.messages)

        # store doesn't support recording (missing the three optional methods) -> the framework
        # doesn't attempt a resume-dispatch decision, and doesn't blow up either
        class Minimal:
            def create_run(self, *a): pass
            def append_chunks(self, *a): pass
            def mark_finished(self, *a, **k): pass
            def mark_interrupted(self, *a): pass
            def get_meta(self, *a): return None
            def get_latest_run_id(self, *a): return ""
            def buffer_range(self, *a): return []
        llm2 = ScriptedLLM([_chunk(content="hi")])
        rt2 = Runtime(llm=llm2, registry=_registry(slow), database=InMemoryDatabase(), run_store=Minimal())
        s2 = await rt2.load_session("s2", owner_id="u1")
        run2 = rt2.start(s2, Query.text("go"))
        _ = [d async for d in run2.subscribe()]
        assert run2.status is RunStatus.DONE and llm2.calls == 1
    asyncio.run(go())
    print("test_no_records_means_no_resume_and_answer_takes_precedence OK")


def test_create_run_is_idempotent_and_preserves_recovery_evidence():
    """A restart rebuilds the same Run without resetting its persistent identity."""
    store = InMemoryRunStore()
    store.create_run("r1", "u1", "s1")
    store.append_chunks("r1", ["before-restart"])
    store.request_stop("r1")
    store.mark_interrupted("r1")
    assert store.mark_resuming("r1") == 1
    before = store.get_meta("r1")
    assert before is not None

    store.create_run("r1", "different-owner", "different-session")
    after = store.get_meta("r1")
    assert after is before
    assert (after.owner_id, after.session_id, after.status, after.resume_count) == (
        "u1", "s1", "running", 1,
    )
    assert store.buffer_range("r1", 0) == ["before-restart"]
    assert store.stop_requested("r1")
    assert store.get_latest_run_id("u1", "s1") == "r1"

    store.mark_finished("r1")
    finished_at = after.finished_at
    store.create_run("r1", "u1", "s1")
    assert after.status == "done" and after.finished_at == finished_at
    print("test_create_run_is_idempotent_and_preserves_recovery_evidence OK")


if __name__ == "__main__":
    test_dispatch_is_recorded_then_resumed_without_llm()
    test_no_records_means_no_resume_and_answer_takes_precedence()
    test_create_run_is_idempotent_and_preserves_recovery_evidence()
