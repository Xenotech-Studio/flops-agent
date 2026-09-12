"""Two hooks into the framework's drive loop: task cancellation must not lose the terminal state;
failure text can be customized via Runner.describe_error.

Cancellation: when the host calls ``run.task.cancel()`` (shutdown / hard cancel), the framework must
still ``finish`` (subscribers get the sentinel, terminal state lands in the store), then re-raise
CancelledError as-is (never swallow it, or shutdown would hang); the session marker is left uncleared
(recovery relies on it).
Error text: the ``Error`` event's message goes through ``describe_error``, defaulting to the raw
exception text; the host can override it with a user-facing message.
"""
import asyncio
import os
import sys
from types import SimpleNamespace as SN

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent import Query, Runner, Runtime, Session  # noqa: E402
from flops_agent.engine.execution import RunStatus  # noqa: E402
from flops_agent.entities import events as ev  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def chunk(**delta):
    d = {"content": None, "reasoning_content": None, "tool_calls": None, **delta}
    return SN(choices=[SN(delta=SN(**d), finish_reason=None)], usage=None)


class SlowLLM:
    """Yields for 50ms between each chunk -- leaves a window for external cancellation."""

    async def acompletion(self, **kw):
        async def gen():
            for i in range(50):
                await asyncio.sleep(0.05)
                yield chunk(content=f"chunk{i}")
        return gen()


class ExplodingLLM:
    async def acompletion(self, **kw):
        raise RuntimeError("provider says: quota exceeded (code 429)")


def test_task_cancel_still_finishes_run_and_reraises():
    async def go():
        runtime = Runtime(llm=SlowLLM())
        run = runtime.start(Session("s1"), Query.text("q"))
        drained = []

        async def subscriber():
            async for d in run.subscribe():
                drained.append(d)

        sub = asyncio.create_task(subscriber())
        await asyncio.sleep(0.12)                  # the stream has already started
        assert run.task is not None
        run.task.cancel()
        results = await asyncio.gather(run.task, return_exceptions=True)
        assert isinstance(results[0], asyncio.CancelledError), "CancelledError must be re-raised as-is"
        assert run.done, "must still finish after cancellation, or subscribers wait forever for the sentinel"
        assert run.status is RunStatus.STOPPED
        await asyncio.wait_for(sub, 1.0)          # the subscriber wakes up and ends normally
        assert runtime.runs.find("s1") is None    # removed from the pool
    _run(go())
    print("test_task_cancel_still_finishes_run_and_reraises OK")


def test_describe_error_shapes_the_error_event():
    async def go():
        class FriendlyRunner(Runner):
            def describe_error(self, exc):
                return "Model quota exhausted, please try again later" if "quota" in str(exc) else str(exc)

        run = Runtime(llm=ExplodingLLM(), runner=FriendlyRunner).start(Session("s1"), Query.text("q"))
        events = [d.event async for d in run]
        errors = [e for e in events if isinstance(e, ev.Error)]
        assert run.status is RunStatus.FAILED
        assert errors and errors[0].message == "Model quota exhausted, please try again later", errors
    _run(go())
    print("test_describe_error_shapes_the_error_event OK")


def test_describe_error_default_is_the_exception_text():
    async def go():
        run = Runtime(llm=ExplodingLLM()).start(Session("s1"), Query.text("q"))
        events = [d.event async for d in run]
        errors = [e for e in events if isinstance(e, ev.Error)]
        assert errors and "quota exceeded" in errors[0].message
    _run(go())
    print("test_describe_error_default_is_the_exception_text OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in _TESTS:
        t()
    print(f"\nALL {len(_TESTS)} CANCEL/ERROR TESTS PASSED")


def test_hard_cancel_after_stop_emits_cancelled_and_runs_on_run_end():
    """The host calls stop() then task.cancel(): the framework must wind down through on_cancelled,
    emit Cancelled, then call on_run_end before re-raising; a shutdown-style cancel (without stop())
    does not emit Cancelled."""
    async def go():
        calls = []

        class R(Runner):
            async def on_cancelled(self):
                calls.append("on_cancelled")

            async def on_run_end(self, status):
                calls.append(("on_run_end", status))

        runtime = Runtime(llm=SlowLLM(), runner=R)
        run = runtime.start(Session("s1"), Query.text("q"))
        got = []

        async def sub():
            async for d in run.subscribe():
                got.append(d.event)

        s = asyncio.create_task(sub())
        await asyncio.sleep(0.12)
        await run.stop()                 # explicit stop ...
        run.task.cancel()                # ... then a hard cancel, without waiting for that to finish
        await asyncio.gather(run.task, return_exceptions=True)
        await asyncio.wait_for(s, 1.0)
        assert "on_cancelled" in calls
        assert ("on_run_end", RunStatus.STOPPED) in calls
        assert any(isinstance(e, ev.Cancelled) for e in got)
    _run(go())
    print("test_hard_cancel_after_stop_emits_cancelled_and_runs_on_run_end OK")


def test_on_run_end_runs_after_finish_on_normal_completion():
    async def go():
        seen = []

        class R(Runner):
            async def on_run_end(self, status):
                seen.append((status, self.run.done))

        run = Runtime(llm=ExplodingLLM(), runner=R).start(Session("s1"), Query.text("q"))
        async for _ in run:
            pass
        await asyncio.gather(run.task, return_exceptions=True)
        assert seen == [(RunStatus.FAILED, True)], seen   # only called after finish has landed the terminal state
    _run(go())
    print("test_on_run_end_runs_after_finish_on_normal_completion OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in _TESTS:
        t()
    print(f"\nALL {len(_TESTS)} CANCEL/ERROR TESTS PASSED")
