"""Run unit tests -- the semantics of separating execution from subscription.

This is the hardest part of the framework to get right (locking, subscribers coming and
going, cursor stitching, teardown), and it's also what distinguishes us from "return a
generator" style agent libraries. Below, every claim in the contract is turned into an assertion:

* Execution keeps advancing even with no subscribers (closing the client tab doesn't affect the background task)
* Multiple subscribers each get their own full copy
* Reconnecting by cursor is exact -- no duplicates, no gaps
* Backfilled history is coalesced segments, live tail is raw events -- the different granularity is intentional
* Interruption only sets a flag; the executor tears down normally on its own
"""
import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent.engine.execution import (  # noqa: E402
    Delivery,
    PassthroughCoalescer,
    Run,
    RunPool,
    RunStatus,
    SessionRunActiveError,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class PairCoalescer:
    """Merges every two events into one segment -- used to create a "log index != event sequence number" situation."""

    def __init__(self):
        self.pending = []

    def feed(self, event):
        self.pending.append(event)
        if len(self.pending) == 2:
            merged = "+".join(self.pending)
            self.pending = []
            return [merged]
        return []

    def flush(self):
        if not self.pending:
            return []
        out = ["+".join(self.pending)]
        self.pending = []
        return out


# ── Execution/subscription separation ───────────────────────────────────────

def test_emits_without_any_subscriber():
    """Execution keeps advancing with no subscribers -- closing the client tab doesn't affect the background task."""
    async def go():
        run = Run("r1", session_id="s1")
        await run.emit("a")
        await run.emit("b")
        assert run.cursor == 2
        assert run.subscriber_count == 0
        await run.finish()
        # A subscriber that arrives afterward can still get the full history
        got = [d.event async for d in run.subscribe(0)]
        assert got == ["a", "b"]
    _run(go())
    print("test_emits_without_any_subscriber OK")


def test_late_subscriber_gets_history_then_live():
    async def go():
        run = Run("r1")
        await run.emit("a")                    # happened before the subscription
        seen = []

        async def sub():
            async for d in run:
                seen.append((d.event, d.replayed))
                if len(seen) == 3:
                    return

        task = asyncio.create_task(sub())
        await asyncio.sleep(0)                 # let the subscriber attach
        await run.emit("b")
        await run.emit("c")
        await asyncio.wait_for(task, 1)
        assert seen == [("a", True), ("b", False), ("c", False)]   # history first, then live
    _run(go())
    print("test_late_subscriber_gets_history_then_live OK")


def test_multiple_subscribers_each_get_everything():
    async def go():
        run = Run("r1")
        got = {"x": [], "y": []}

        async def sub(name):
            async for d in run:
                got[name].append(d.event)

        t1 = asyncio.create_task(sub("x"))
        t2 = asyncio.create_task(sub("y"))
        await asyncio.sleep(0)
        await run.emit("a")
        await run.emit("b")
        await run.finish()
        await asyncio.wait_for(asyncio.gather(t1, t2), 1)
        assert got["x"] == ["a", "b"] and got["y"] == ["a", "b"]
    _run(go())
    print("test_multiple_subscribers_each_get_everything OK")


# ── Cursor: reconnecting is exact, no duplicates, no gaps ───────────────────

def test_reconnect_from_cursor_is_exact():
    async def go():
        run = Run("r1")
        for e in ["a", "b", "c"]:
            await run.emit(e)
        await run.finish()
        first = [d async for d in run.subscribe(0)]
        assert [d.event for d in first] == ["a", "b", "c"]
        # "Disconnected" after receiving the second one: resume from its cursor
        resumed = [d.event async for d in run.subscribe(first[1].cursor)]
        assert resumed == ["c"]                       # no duplicates, no gaps
    _run(go())
    print("test_reconnect_from_cursor_is_exact OK")


def test_subscribe_from_current_cursor_gets_only_new():
    async def go():
        run = Run("r1")
        await run.emit("old")
        start = run.cursor
        seen = []

        async def sub():
            async for d in run.subscribe(start):
                seen.append(d.event)

        task = asyncio.create_task(sub())
        await asyncio.sleep(0)
        await run.emit("new")
        await run.finish()
        await asyncio.wait_for(task, 1)
        assert seen == ["new"]                        # does not backfill old ones
    _run(go())
    print("test_subscribe_from_current_cursor_gets_only_new OK")


def test_cursor_is_not_event_count_when_coalescing():
    """The executable version of the core warning: after coalescing, log index != event sequence number, clients can't just count."""
    async def go():
        run = Run("r1", coalescer=PairCoalescer())
        for e in ["a", "b", "c", "d"]:
            await run.emit(e)
        assert run.cursor == 2                        # 4 events -> 2 segments
        await run.finish()
        got = [d.event async for d in run.subscribe(0)]
        assert got == ["a+b", "c+d"]                  # history is coalesced segments, not raw events
    _run(go())
    print("test_cursor_is_not_event_count_when_coalescing OK")


def test_flush_on_finish_keeps_tail():
    """The coalescer is stateful: without a flush, the segment still being accumulated would be lost."""
    async def go():
        run = Run("r1", coalescer=PairCoalescer())
        await run.emit("a")
        await run.emit("b")
        await run.emit("c")                           # odd one out, still pending in the coalescer
        assert run.cursor == 1
        await run.finish()
        assert run.cursor == 2
        got = [d.event async for d in run.subscribe(0)]
        assert got == ["a+b", "c"]                    # the tail wasn't lost
    _run(go())
    print("test_flush_on_finish_keeps_tail OK")


def test_passthrough_cursor_equals_event_index():
    async def go():
        run = Run("r1", coalescer=PassthroughCoalescer())
        await run.emit("a")
        await run.emit("b")
        assert run.cursor == 2                        # without coalescing, cursor equals event sequence number
    _run(go())
    print("test_passthrough_cursor_equals_event_index OK")


# ── Terminal states and interruption ─────────────────────────────────────────

def test_finish_wakes_subscribers_and_is_idempotent():
    async def go():
        run = Run("r1")
        done = asyncio.Event()

        async def sub():
            async for _ in run:
                pass
            done.set()

        asyncio.create_task(sub())
        await asyncio.sleep(0)
        await run.finish()
        await asyncio.wait_for(done.wait(), 1)        # subscriber is woken up and finishes
        assert run.done and run.status is RunStatus.DONE
        await run.finish(RunStatus.FAILED)            # idempotent: does not change the terminal state
        assert run.status is RunStatus.DONE
    _run(go())
    print("test_finish_wakes_subscribers_and_is_idempotent OK")


def test_stop_only_sets_flag():
    """Interruption doesn't force-kill -- the executor sees the flag at a checkpoint and tears down normally on its own."""
    async def go():
        run = Run("r1")
        assert run.stop_requested is False
        await run.stop()
        assert run.stop_requested is True
        assert run.done is False                      # not finished yet, waiting for the executor to tear down
        await run.emit("still-emitting")              # the flag does not block writes
        await run.finish(RunStatus.STOPPED)
        assert run.status is RunStatus.STOPPED
    _run(go())
    print("test_stop_only_sets_flag OK")


def test_wait_returns_final_status():
    async def go():
        run = Run("r1")
        asyncio.create_task(run.finish(RunStatus.SUSPENDED))
        assert await asyncio.wait_for(run.wait(), 1) is RunStatus.SUSPENDED
    _run(go())
    print("test_wait_returns_final_status OK")


def test_failed_run_carries_error():
    async def go():
        run = Run("r1")
        err = ValueError("boom")
        await run.finish(RunStatus.FAILED, error=err)
        assert run.status is RunStatus.FAILED and run.error is err
    _run(go())
    print("test_failed_run_carries_error OK")


# ── RunPool ────────────────────────────────────────────────────────────────

def test_pool_finds_by_session_and_id():
    run = Run("r1", session_id="s1")
    pool = RunPool()
    pool.add(run)
    assert pool.find("s1") is run
    assert pool.get("r1") is run
    assert pool.find("nope") is None and len(pool) == 1
    pool.discard(run)
    assert pool.find("s1") is None and len(pool) == 0
    print("test_pool_finds_by_session_and_id OK")


def test_pool_rejects_second_active_run_for_session():
    """A second run cannot overwrite the active-session index."""
    pool = RunPool()
    old, new = Run("r1", session_id="s1"), Run("r2", session_id="s1")
    pool.add(old)
    try:
        pool.add(new)
    except SessionRunActiveError as exc:
        assert exc.session_id == "s1" and exc.run_id == "r1"
    else:
        raise AssertionError("second active run must be rejected")
    assert pool.find("s1") is old and pool.get("r2") is None
    print("test_pool_rejects_second_active_run_for_session OK")


def test_pool_isolates_sessions_and_subscriptions():
    """Concurrent sessions remain separately addressable and never cross-deliver."""
    async def go():
        pool = RunPool()
        one, two = Run("r1", session_id="s1"), Run("r2", session_id="s2")
        pool.add(one)
        pool.add(two)
        assert pool.find("s1") is one and pool.find("s2") is two
        got_one, got_two = [], []

        async def collect(run, target):
            async for delivery in run:
                target.append(delivery.event)

        left = asyncio.create_task(collect(one, got_one))
        right = asyncio.create_task(collect(two, got_two))
        await asyncio.sleep(0)
        await one.emit("only-one")
        await two.emit("only-two")
        await one.finish()
        await two.finish()
        await asyncio.gather(left, right)
        assert got_one == ["only-one"] and got_two == ["only-two"]

    _run(go())
    print("test_pool_isolates_sessions_and_subscriptions OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in _TESTS:
        t()
    print(f"\nALL {len(_TESTS)} RUN TESTS PASSED")
