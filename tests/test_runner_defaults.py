"""Default behavior that comes free from the base class -- things a product should
get without writing a single line of code.

These aren't "optional features," they're the correctness baseline for a
service-style agent: a mid-run crash shouldn't lose progress, the client should
know when history changed, and new input arriving mid-work shouldn't be left
hanging. If a third party implements these three things itself, getting it
wrong means lost data, a scrambled UI, or ignored messages.

This also verifies the Database protocol's write granularity is right: the
common case (appending a few messages) writes only those messages -- no need to
rewrite the whole segment, and no need for the caller to tell the backend "I
only touched the tail this time."
"""
import asyncio
import os
import sys
from types import SimpleNamespace as SN

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent import Query, Runner, Runtime, Session  # noqa: E402
from flops_agent.entities import events as ev  # noqa: E402
from flops_agent.seams.database import InMemoryDatabase, sync_session  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def chunk(**delta):
    d = {"content": None, "reasoning_content": None, "tool_calls": None, **delta}
    return SN(choices=[SN(delta=SN(**d), finish_reason=None)], usage=None)


def tool_chunk(name, args="{}"):
    return chunk(tool_calls=[SN(index=0, id="c1", type="function",
                                function=SN(name=name, arguments=args))])


class FakeLLM:
    def __init__(self, *steps):
        self.steps, self.calls = list(steps), 0

    async def acompletion(self, **kw):
        step = self.steps[self.calls] if self.calls < len(self.steps) else []
        self.calls += 1

        async def gen():
            for c in step:
                yield c
        return gen()


def ping():
    """A tool."""
    return {"ok": True}


class RecordingDatabase(InMemoryDatabase):
    """Records every write operation, used to verify "only the delta is written"."""

    def __init__(self):
        super().__init__()
        self.writes = []

    def append_messages(self, session_id, messages, **kw):
        self.writes.append(("append", len(messages)))
        super().append_messages(session_id, messages, **kw)

    def truncate_messages(self, session_id, *, keep, **kw):
        self.writes.append(("truncate", keep))
        super().truncate_messages(session_id, keep=keep, **kw)


# ── Persistence is the default behavior ─────────────────────────────────────

def test_persists_without_product_writing_any_code():
    async def go():
        db = InMemoryDatabase()
        db.create_session("s1")
        runtime = Runtime(llm=FakeLLM([chunk(content="answer")]), database=db)
        await runtime.start(Session("s1"), Query.text("question")).wait()
        stored = db.load_messages("s1")
        assert [m["content"] for m in stored] == ["question", "answer"]
    _run(go())
    print("test_persists_without_product_writing_any_code OK")


def test_persists_after_each_step_not_only_at_end():
    """A turn that runs multiple steps shouldn't lose the earlier steps if step N crashes."""
    seen_counts = []

    class CrashAfterSecondStep(Runner):
        async def persist(self):
            await super().persist()
            seen_counts.append(self.runtime.database.count_messages("s1"))

    async def go():
        db = InMemoryDatabase()
        db.create_session("s1")
        runtime = Runtime(
            llm=FakeLLM([tool_chunk("ping")], [tool_chunk("ping")], [chunk(content="done")]),
            tools=[ping], database=db, runner=CrashAfterSecondStep,
        )
        await runtime.start(Session("s1"), Query.text("q")).wait()
        # Persisted mid-run, not just once at the very end.
        assert len(seen_counts) >= 3 and seen_counts[0] < seen_counts[-1]
    _run(go())
    print("test_persists_after_each_step_not_only_at_end OK")


def test_persists_even_when_stopped():
    async def go():
        db = InMemoryDatabase()
        db.create_session("s1")
        gate, entered = asyncio.Event(), asyncio.Event()

        class Blocking:
            async def acompletion(self, **kw):
                async def gen():
                    entered.set()
                    await gate.wait()
                    yield chunk(content="late")
                return gen()

        runtime = Runtime(llm=Blocking(), database=db)
        run = runtime.start(Session("s1"), Query.text("question"))
        await asyncio.wait_for(entered.wait(), 1)
        await run.stop()
        gate.set()
        await asyncio.wait_for(run.wait(), 1)
        assert [m["content"] for m in db.load_messages("s1")] == ["question"]   # the user message wasn't lost
    _run(go())
    print("test_persists_even_when_stopped OK")


def test_no_database_is_fine():
    """Runs fine without a configured database -- the minimal path shouldn't be forced to set up storage first."""
    async def go():
        runtime = Runtime(llm=FakeLLM([chunk(content="hi")]))
        await runtime.start(Session("s1"), Query.text("q")).wait()
    _run(go())
    print("test_no_database_is_fine OK")


# ── Only the delta is written (a check on protocol granularity) ─────────────

def test_only_the_delta_is_written():
    """The common case is appending a few messages -- it shouldn't rewrite the whole segment, nor need a hint like "I only touched the tail"."""
    async def go():
        db = RecordingDatabase()
        db.create_session("s1")
        session = Session("s1")
        runtime = Runtime(llm=FakeLLM([chunk(content="A")], [chunk(content="B")]),
                          database=db)
        await runtime.start(session, Query.text("one")).wait()
        await runtime.start(session, Query.text("two")).wait()
        assert all(kind == "append" for kind, _ in db.writes)
        assert all(n <= 2 for _, n in db.writes)      # each write is just the one or two new messages
    _run(go())
    print("test_only_the_delta_is_written OK")


def test_truncation_syncs_as_truncate():
    """History gets shorter after a regeneration -- sync it as a single truncation, not a full rewrite."""
    db = RecordingDatabase()
    db.create_session("s1")
    session = Session("s1", messages=[
        {"external_id": "u1", "role": "user", "content": "q"},
        {"external_id": "a1", "role": "assistant", "content": "old"},
    ])
    sync_session(db, session)
    db.writes.clear()
    session.truncate_after("u1")
    assert sync_session(db, session) == -1
    assert db.writes == [("truncate", 1)]
    print("test_truncation_syncs_as_truncate OK")


# ── History-changed notification ─────────────────────────────────────────────

def test_history_changed_is_emitted_with_revision():
    async def go():
        db = InMemoryDatabase()
        db.create_session("s1")
        runtime = Runtime(llm=FakeLLM([tool_chunk("ping")], [chunk(content="done")]),
                          tools=[ping], database=db)
        run = runtime.start(Session("s1"), Query.text("q"))
        events = [d.event async for d in run]
        changes = [e for e in events if isinstance(e, ev.HistoryChanged)]
        assert changes, "the client should be notified that history changed"
        assert changes[0].revision == 1
        assert changes[-1].message_count == 4          # user + assistant + tool + assistant
    _run(go())
    print("test_history_changed_is_emitted_with_revision OK")


# ── New input arriving at a turn boundary ────────────────────────────────────

def test_pending_input_continues_the_turn():
    """The user sends another message while the agent is still working -- it shouldn't be left hanging waiting for a resend."""
    class QueuedRunner(Runner):
        delivered = False

        async def pending_input(self):
            if QueuedRunner.delivered:
                return []
            QueuedRunner.delivered = True
            return [{"role": "user", "content": "one more thing"}]

    async def go():
        QueuedRunner.delivered = False
        llm = FakeLLM([chunk(content="first answer")], [chunk(content="second answer")])
        runtime = Runtime(llm=llm, runner=QueuedRunner)
        session = Session("s1")
        await runtime.start(session, Query.text("question")).wait()
        assert llm.calls == 2                          # not finished, ran another step
        assert [m["content"] for m in session.messages] == [
            "question", "first answer", "one more thing", "second answer",
        ]
    _run(go())
    print("test_pending_input_continues_the_turn OK")


def test_no_pending_input_ends_the_turn():
    async def go():
        llm = FakeLLM([chunk(content="answer")], [chunk(content="should not happen")])
        await Runtime(llm=llm).start(Session("s1"), Query.text("q")).wait()
        assert llm.calls == 1
    _run(go())
    print("test_no_pending_input_ends_the_turn OK")


# ── Trade-offs when overriding ────────────────────────────────────────────────

def test_override_smaller_method_keeps_persistence():
    """A product that only wants to change the notification mechanism can override notify_history_changed alone; persistence keeps working as before."""
    class CustomNotify(Runner):
        async def notify_history_changed(self):
            await self.emit(ev.ProductEvent("my_sync", {"n": len(self.session)}))

    async def go():
        db = InMemoryDatabase()
        db.create_session("s1")
        runtime = Runtime(llm=FakeLLM([chunk(content="a")]), database=db,
                          runner=CustomNotify)
        run = runtime.start(Session("s1"), Query.text("q"))
        events = [d.event async for d in run]
        assert db.count_messages("s1") == 2                       # persistence still happened
        assert any(isinstance(e, ev.ProductEvent) for e in events)
        assert not any(isinstance(e, ev.HistoryChanged) for e in events)
    _run(go())
    print("test_override_smaller_method_keeps_persistence OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in _TESTS:
        t()
    print(f"\nALL {len(_TESTS)} DEFAULT-BEHAVIOR TESTS PASSED")
