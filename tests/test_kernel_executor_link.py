"""Executor-side delivery hub (flops_agent.executor): dispatch / streaming replies / terminal-state dedup / parking when no one is waiting / resume waits without resending."""
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent import ExecutorLink, InMemoryDispatchLedger  # noqa: E402
from flops_agent.executor import protocol as P  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def test_dispatch_streams_deltas_and_returns_result():
    async def go():
        link = ExecutorLink()
        sent, sink = [], []

        async def send(m):
            sent.append(m)

        msg = P.run_tool("t1", "run_command", {"command": "ls"}, conversation_id="c1")
        task = asyncio.create_task(link.dispatch(task_id="t1", message=msg, send=send, stream_sink=sink.append, timeout=5))
        await asyncio.sleep(0)
        assert sent == [msg] and msg["type"] == "run_tool" and msg["conversation_id"] == "c1"
        assert link.waiting("t1")
        assert link.receive(P.stream_chunk("t1", "a\n")) is None
        assert link.receive(P.stream_meta("t1", {"pid": 3})) is None
        assert link.receive(P.stream_meta("t1", {})) is None            # an empty snapshot isn't forwarded
        assert link.receive({"type": "stream_chunk", "task_id": "t1", "chunk": "", "patches": [1]}) is None
        ack = link.receive(P.result("t1", {"ok": True}))
        assert ack == P.result_ack("t1"), "a terminal state must always get an ack"
        assert await task == {"ok": True}
        assert sink == [{"stdout_append": "a\n"}, {"set": {"pid": 3}}, {"patches": [1]}]
        assert not link.waiting("t1")
        # Duplicate terminal state (executor-side outbox resend): only ack, no redelivery.
        assert link.receive(P.result("t1", {"ok": False})) == P.result_ack("t1")
        # Empty result -> success
        task2 = asyncio.create_task(link.dispatch(task_id="t1b", message=P.run_tool("t1b", "x", {}), send=send, timeout=5))
        await asyncio.sleep(0)
        link.receive(P.result("t1b", None))
        assert await task2 == {"success": True}
        # failed -> success=False + error
        task3 = asyncio.create_task(link.dispatch(task_id="t1c", message=P.run_tool("t1c", "x", {}), send=send, timeout=5))
        await asyncio.sleep(0)
        link.receive(P.failed("t1c", "boom"))
        assert await task3 == {"success": False, "error": "boom"}
    _run(go())
    print("test_dispatch_streams_deltas_and_returns_result OK")


def test_unattended_outcome_is_parked_and_drained_on_resume():
    async def go():
        ledger = InMemoryDispatchLedger()
        link = ExecutorLink(ledger=ledger)
        # No one is waiting on this task (the worker died before the process restarted): park it + ack.
        assert link.receive(P.failed("t2", "boom")) == P.result_ack("t2")
        assert ledger.take_parked("t2") == {"type": "failed", "task_id": "t2", "result": None, "error": "boom"}
        ledger.park("t2", {"type": "failed", "task_id": "t2", "result": None, "error": "boom"})
        # Resume: drain the parked result first, don't send run_tool (send=None is fine too).
        out = await link.dispatch(task_id="t2", message={}, send=None, timeout=1, resume=True)
        assert out == {"success": False, "error": "boom"}
        assert ledger.take_parked("t2") is None, "taking it deletes it"
        # Resume, nothing parked: register as waiting until the executor reconnects and replays.
        task = asyncio.create_task(link.dispatch(task_id="t3", message={}, send=None, timeout=5, resume=True))
        await asyncio.sleep(0)
        assert link.waiting("t3")
        assert link.receive(P.result("t3", {"v": 1})) == P.result_ack("t3")
        assert await task == {"v": 1}
    _run(go())
    print("test_unattended_outcome_is_parked_and_drained_on_resume OK")


def test_timeout_unknown_messages_and_missing_transport():
    async def go():
        link = ExecutorLink()
        sent = []

        async def send(m):
            sent.append(m)

        out = await link.dispatch(task_id="t4", message=P.run_tool("t4", "x", {}), send=send, timeout=0.05)
        assert out == {"success": False, "error": "Timeout (0.05s)"} and len(sent) == 1
        assert not link.waiting("t4")
        # Messages outside the delivery protocol, or missing task_id: ignored.
        assert link.receive({"type": "task_started", "task_id": "x"}) is None
        assert link.receive({"type": "result"}) is None
        assert not link.handles("auth") and link.handles("result")
        # A non-resume dispatch with no transport -> raises explicitly, doesn't silently hang.
        try:
            await link.dispatch(task_id="t5", message={}, send=None, timeout=1)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError")
        # A broken ledger must not blow up dispatch: treated as never-seen / nothing-parked.
        class Broken:
            def seen(self, t): raise RuntimeError("redis down")
            def mark_seen(self, t): raise RuntimeError("redis down")
            def park(self, t, o): raise RuntimeError("redis down")
            def take_parked(self, t): raise RuntimeError("redis down")
        link2 = ExecutorLink(ledger=Broken())
        task = asyncio.create_task(link2.dispatch(task_id="t6", message=P.run_tool("t6", "x", {}), send=send, timeout=5))
        await asyncio.sleep(0)
        assert link2.receive(P.result("t6", {"ok": 1})) == P.result_ack("t6")
        assert await task == {"ok": 1}
    _run(go())
    print("test_timeout_unknown_messages_and_missing_transport OK")


def test_cancel_and_auth_vocabulary():
    assert P.cancel_tool("t") == {"type": "cancel_tool", "task_id": "t"}
    a = P.auth("dev-1", "Laptop", ["executor.shell"], access_token="tok")
    assert a["type"] == "auth" and a["capabilities"] == ["executor.shell"] and a["access_token"] == "tok"
    assert P.auth_ok("dev-1")["device_id"] == "dev-1" and P.auth_fail("bad")["error"] == "bad"
    assert P.outcome_to_tool_result({"type": "stream_chunk"}) is None
    print("test_cancel_and_auth_vocabulary OK")


if __name__ == "__main__":
    test_dispatch_streams_deltas_and_returns_result()
    test_unattended_outcome_is_parked_and_drained_on_resume()
    test_timeout_unknown_messages_and_missing_transport()
    test_cancel_and_auth_vocabulary()
