"""Slow synchronous backends must not stall Runtime's event loop."""
import asyncio
import os
import sys
import time
from types import SimpleNamespace as SN

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent import InMemoryDatabase, InMemoryRunStore, Query, Run, Runtime, Session  # noqa: E402


class SlowRunStore(InMemoryRunStore):
    """A deterministic 50ms blocking backend used to catch loop regressions."""

    delay = 0.05

    def __init__(self):
        super().__init__()
        self.append_in_flight = 0
        self.max_append_in_flight = 0

    def create_run(self, run_id, owner_id, session_id):
        time.sleep(self.delay)
        super().create_run(run_id, owner_id, session_id)

    def append_chunks(self, run_id, parts):
        self.append_in_flight += 1
        self.max_append_in_flight = max(self.max_append_in_flight, self.append_in_flight)
        try:
            time.sleep(self.delay)
            super().append_chunks(run_id, parts)
        finally:
            self.append_in_flight -= 1


class SlowMarkerDatabase(InMemoryDatabase):
    delay = 0.05

    def patch_meta(self, session_id, fields, *, owner_id="", keys=None):
        time.sleep(self.delay)
        super().patch_meta(session_id, fields, owner_id=owner_id, keys=keys)


class MarkerCheckingLLM:
    def __init__(self, database):
        self.database = database
        self.called = False

    async def acompletion(self, **kwargs):
        self.called = True
        assert (self.database.load_meta("s1") or {}).get("active_run_id")

        async def stream():
            yield SN(choices=[SN(delta=SN(content="ok", reasoning_content=None, tool_calls=None), finish_reason=None)], usage=None)

        return stream()


async def _ticker_until(done):
    ticks = 0
    while not done.is_set():
        ticks += 1
        await asyncio.sleep(0.005)
    return ticks


def test_run_store_preload_and_writes_do_not_block_subscription_or_loop():
    async def go():
        store = SlowRunStore()
        run = Run("r1", session_id="s1", store=store)
        done = asyncio.Event()
        ticker = asyncio.create_task(_ticker_until(done))
        got = []

        async def collect():
            async for delivery in run:
                got.append(delivery.event)

        subscriber = asyncio.create_task(collect())
        await asyncio.sleep(0.005)  # subscriber owns store initialization first
        await asyncio.wait_for(
            asyncio.gather(run.emit("one"), run.emit("two")), 1
        )
        await run.finish()
        await asyncio.wait_for(subscriber, 1)
        done.set()
        assert await ticker >= 10, "50ms store calls must leave the loop schedulable"
        assert got == ["one", "two"]
        assert store.buffer_range("r1", 0) == ["one", "two"]
        assert store.max_append_in_flight == 1, "same Run writes must remain ordered"

    asyncio.run(go())
    print("test_run_store_preload_and_writes_do_not_block_subscription_or_loop OK")


def test_start_returns_before_slow_marker_io_but_runner_waits_for_it():
    async def go():
        database = SlowMarkerDatabase()
        llm = MarkerCheckingLLM(database)
        runtime = Runtime(llm=llm, database=database)
        session = Session("s1")
        started = time.perf_counter()
        run = runtime.start(session, Query.text("hello"))
        assert time.perf_counter() - started < 0.025
        assert session.meta.get("active_run_id") == run.id

        done = asyncio.Event()
        ticker = asyncio.create_task(_ticker_until(done))
        _ = [delivery async for delivery in run]
        await run.task
        done.set()
        assert await ticker >= 10, "50ms marker writes must not freeze the loop"
        assert llm.called

    asyncio.run(go())
    print("test_start_returns_before_slow_marker_io_but_runner_waits_for_it OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for test in _TESTS:
        test()
    print(f"\\nALL {len(_TESTS)} NONBLOCKING PERSISTENCE TESTS PASSED")
