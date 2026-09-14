"""Framework session active-run marker -- single-owner lifecycle (written on start / cleared by the
terminal-state guard).

Background (2026-07-23 incident): the persisted "this session has a run in flight" marker used to be
maintained by multiple host writers -- a stale snapshot's full save would wipe out the live pointer,
making an autonomous run "invisible" to the frontend and causing a 409 on resume. After moving this
into the framework, four things are pinned down:

1. ``Runtime.start`` writes the marker onto ``session.meta`` (the host can rename the field) and
   persists it surgically via ``Database.patch_meta`` -- never a full save;
2. re-entering with the same run_id (process-restart recovery resuming a run) does not refresh
   started_at;
3. the terminal-state guard only clears when the current value is itself -> clear; points to another
   live run -> leave alone; points to a dead run -> clear it too;
4. a ``patch_meta`` value of None means "delete this field" (a protocol convention).

Run: ``python3 backend/tests/test_kernel_active_run.py`` (or via pytest).
"""
import asyncio
import os
import sys
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent import Runtime, InMemoryDatabase, Session  # noqa: E402


def _chunk(content=None, finish=None):
    delta = SimpleNamespace(content=content, reasoning_content=None, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)],
                           usage=None)


class OneShotLLM:
    async def acompletion(self, **kw):
        async def _stream():
            yield _chunk(content="done")
            yield _chunk(finish="stop")
        return _stream()


def test_patch_meta_none_removes_field():
    db = InMemoryDatabase()
    db.patch_meta("s", {"a": 1, "b": 2})
    db.patch_meta("s", {"a": None})
    assert db.load_meta("s") == {"b": 2}
    print("test_patch_meta_none_removes_field OK")


def test_start_marks_and_terminal_clears():
    db = InMemoryDatabase()
    runtime = Runtime(llm=OneShotLLM(), tools=[], database=db)
    session = Session("s1", owner_id="u1",
                      messages=[{"role": "user", "content": "hi"}])

    async def _go():
        run = runtime.start(session)
        # the synchronous part of start() already wrote the marker: in-memory meta + surgical database persist
        meta = db.load_meta("s1") or {}
        assert meta.get("active_run_id") == run.id, meta
        assert meta.get("active_run_started_at")
        assert session.meta.get("active_run_id") == run.id
        async for _ in run:
            pass
        await run.task            # drive()'s teardown (including unmarking) happens after finish
        return run

    asyncio.run(_go())
    meta = db.load_meta("s1") or {}
    assert "active_run_id" not in meta, meta
    assert "active_run_started_at" not in meta, meta
    assert "active_run_id" not in (session.meta or {})
    print("test_start_marks_and_terminal_clears OK")


def test_resume_same_run_id_preserves_started_at():
    db = InMemoryDatabase()
    runtime = Runtime(llm=OneShotLLM(), tools=[], database=db)
    session = Session("s2", owner_id="u1",
                      messages=[{"role": "user", "content": "hi"}],
                      meta={"active_run_id": "run_x",
                            "active_run_started_at": "2026-01-01T00:00:00"})
    db.patch_meta("s2", dict(session.meta))
    runtime._mark_session_active_run(session, "run_x")     # re-entering with the same run
    assert session.meta["active_run_started_at"] == "2026-01-01T00:00:00"
    assert (db.load_meta("s2") or {}).get("active_run_started_at") == "2026-01-01T00:00:00"
    runtime._mark_session_active_run(session, "run_y")     # switching to a new run -> refresh
    assert session.meta["active_run_id"] == "run_y"
    assert session.meta["active_run_started_at"] != "2026-01-01T00:00:00"
    print("test_resume_same_run_id_preserves_started_at OK")


def test_guarded_clear_respects_concurrent_live_run():
    """A late-finishing old run must never wrongly clear a concurrent new run's marker; a stale dead
    pointer, however, gets cleared."""
    db = InMemoryDatabase()
    runtime = Runtime(llm=OneShotLLM(), tools=[], database=db)
    session = Session("s3", owner_id="u1")

    # Scenario A: the marker belongs to a new run still alive in the pool -> the old run's clear must back off
    live = SimpleNamespace(id="run_new", done=False,
                           session_id="s3", owner_id="u1")
    runtime.runs.add(live)
    db.patch_meta("s3", {"active_run_id": "run_new",
                         "active_run_started_at": "t"})
    runtime._clear_session_active_run(session, "run_old")
    assert (db.load_meta("s3") or {}).get("active_run_id") == "run_new"

    # Scenario B: the run the marker points to is dead (outside the pool + store says terminal) -> stale leftover, clear it
    runtime.runs.discard(live)
    store = SimpleNamespace(
        get_meta=lambda rid: SimpleNamespace(status="done"),
    )
    runtime.run_store = store
    runtime._clear_session_active_run(session, "run_old")
    meta = db.load_meta("s3") or {}
    assert "active_run_id" not in meta, meta

    # Scenario C: the marker is itself -> clear (without consulting the store)
    db.patch_meta("s3", {"active_run_id": "run_me", "active_run_started_at": "t"})
    runtime._clear_session_active_run(session, "run_me")
    assert "active_run_id" not in (db.load_meta("s3") or {})
    print("test_guarded_clear_respects_concurrent_live_run OK")


def test_host_field_name_override():
    """A host can plug in a legacy field name (e.g. Flops's active_chat_v2_run_id) with zero protocol changes."""
    class LegacySession(Session):
        active_run_field = "active_chat_v2_run_id"
        active_run_started_field = "active_chat_v2_run_started_at"

    db = InMemoryDatabase()
    runtime = Runtime(llm=OneShotLLM(), tools=[], database=db)
    session = LegacySession("s4", owner_id="u1")
    runtime._mark_session_active_run(session, "r1")
    assert (db.load_meta("s4") or {}).get("active_chat_v2_run_id") == "r1"
    runtime._clear_session_active_run(session, "r1")
    assert "active_chat_v2_run_id" not in (db.load_meta("s4") or {})
    print("test_host_field_name_override OK")


def test_suspended_marker_lifecycle():
    """The suspended marker (in the same family as the active marker): mark persists; a normal
    finish's guard clears it; another run's live marker is never wrongly cleared."""
    db = InMemoryDatabase()
    runtime = Runtime(llm=OneShotLLM(), tools=[], database=db)
    session = Session("s5", owner_id="u1")

    runtime.mark_session_suspended(session, {"kind": "ask", "run_id": "r_susp"})
    assert (db.load_meta("s5") or {}).get("pending_interaction", {}).get("kind") == "ask"

    # the marker belongs to another run that is still "alive" (store status=suspended, non-terminal) -> the finish guard backs off
    runtime.run_store = SimpleNamespace(get_meta=lambda rid: SimpleNamespace(status="suspended"))
    runtime._clear_session_suspended(session, "r_other")
    assert "pending_interaction" in (db.load_meta("s5") or {})

    # explicit clear (resuming re-entry, empty run_id = unconditional) -> clear
    runtime.clear_session_suspended("s5", owner_id="u1", reason="resumed")
    assert "pending_interaction" not in (db.load_meta("s5") or {})

    # self-healing from a zombie: the marker points to a dead run, any run's finish guard clears it
    runtime.mark_session_suspended(session, {"kind": "ask", "run_id": "r_dead"})
    runtime.run_store = SimpleNamespace(get_meta=lambda rid: SimpleNamespace(status="done"))
    runtime._clear_session_suspended(session, "r_current")
    assert "pending_interaction" not in (db.load_meta("s5") or {})
    print("test_suspended_marker_lifecycle OK")


def test_suspend_run_auto_marks_at_drive_end():
    """Safety net for when the host doesn't mark it itself at the decision point: a suspending finish
    auto-writes the marker (including a run_id fallback)."""
    class SuspendingLLM:
        async def acompletion(self, **kw):
            async def _stream():
                yield _chunk(content="need you to decide")
                yield _chunk(finish="stop")
            return _stream()

    from flops_agent import Runner as _Runner
    from flops_agent.engine.interaction import Interaction

    class SuspendRunner(_Runner):
        async def build_reply(self):
            reply = await super().build_reply()
            self.suspend_marker = {"kind": "demo"}     # simulate a suspend decision
            return reply

    db = InMemoryDatabase()
    runtime = Runtime(llm=SuspendingLLM(), tools=[], database=db, runner=SuspendRunner)
    session = Session("s6", owner_id="u1", messages=[{"role": "user", "content": "hi"}])

    async def _go():
        run = runtime.start(session)
        async for _ in run:
            pass
        await run.task
        return run

    run = asyncio.run(_go())
    stored = (db.load_meta("s6") or {}).get("pending_interaction")
    assert stored and stored.get("kind") == "demo" and stored.get("run_id") == run.id, stored
    print("test_suspend_run_auto_marks_at_drive_end OK")


def test_sync_session_equal_count_replaces_tail():
    """sync_session covers three shapes: equal length = the last record may have changed (finish
    swapped the tail / continuation / re-answer), so only the last record is rewritten.
    Real-world lesson: a host's fat-save append-only probe can't detect an equal-length tail swap;
    on an 8k-message session, a full rewrite on every turn's finish took ~8s."""
    from flops_agent import InMemoryDatabase, Session
    from flops_agent.seams.database import sync_session

    db = InMemoryDatabase()
    db.create_session("s9", owner_id="u1")
    db.append_messages("s9", [
        {"role": "user", "content": "q", "external_id": "m1"},
        {"role": "assistant", "content": "old answer", "external_id": "m2"},
    ], owner_id="u1")

    session = Session("s9", owner_id="u1", messages=[
        {"role": "user", "content": "q", "external_id": "m1"},
        {"role": "assistant", "content": "new answer (tail swapped on finish)", "external_id": "m3"},
    ])
    delta = sync_session(db, session)
    assert delta == 0
    stored = db.load_messages("s9", owner_id="u1")
    assert len(stored) == 2 and stored[-1]["content"] == "new answer (tail swapped on finish)", stored
    # append and truncate shapes remain unaffected by this regression
    session.messages.append({"role": "user", "content": "next", "external_id": "m4"})
    assert sync_session(db, session) == 1
    del session.messages[1:]
    assert sync_session(db, session) == -2
    assert len(db.load_messages("s9", owner_id="u1")) == 1
    print("test_sync_session_equal_count_replaces_tail OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in _TESTS:
        t()
    print(f"\nALL {len(_TESTS)} TESTS PASSED")
