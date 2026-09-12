"""Framework contract for automatically retrying an LLM stream that fails mid-flight."""

import asyncio
from types import SimpleNamespace as SN

from flops_agent import Query, RunStatus, Runtime, Session
from flops_agent.engine.runtime import LLMStreamRetryPolicy
from flops_agent.entities import events as ev


def chunk(**delta):
    values = {"content": None, "reasoning_content": None, "tool_calls": None, **delta}
    return SN(choices=[SN(delta=SN(**values), finish_reason=None)], usage=None)


class StreamBroken(Exception):
    """Simulates a stream breaking mid-flight; status_code lets the retry classifier categorize it."""

    def __init__(self, status_code: int = 503):
        super().__init__(f"stream broken ({status_code})")
        self.status_code = status_code


class ScriptedLLM:
    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.calls = 0

    async def acompletion(self, **_kwargs):
        script = self.scripts[self.calls] if self.calls < len(self.scripts) else ([], None)
        self.calls += 1

        async def stream():
            for item in script[0]:
                yield item
            if script[1] is not None:
                raise script[1]

        return stream()


def drive(runtime: Runtime):
    async def run_once():
        session = Session("s1")
        run = runtime.start(session, Query.text("hi"))
        events = [delivery.event async for delivery in run]
        return run, events, session

    return asyncio.new_event_loop().run_until_complete(run_once())


def policy(**kwargs):
    kwargs.setdefault("backoffs", (0, 0))
    return LLMStreamRetryPolicy(**kwargs)


def test_midstream_retry_completes():
    llm = ScriptedLLM(
        ([chunk(content="H")], StreamBroken(503)),
        ([chunk(content="Hello"), chunk(content=" world")], None),
    )
    run, events, session = drive(Runtime(llm=llm, llm_stream_retry=policy(max_attempts=3)))

    assert run.status is RunStatus.DONE
    assert llm.calls == 2
    retries = [event for event in events if isinstance(event, ev.LLMStreamRetrying)]
    assert len(retries) == 1
    assert (retries[0].attempt, retries[0].max_attempts, retries[0].partial_len) == (1, 3, 1)
    assert "".join(event.text for event in events if isinstance(event, ev.TextDelta)) == "HHello world"
    assistants = [message for message in session.messages if message.get("role") == "assistant"]
    assert len(assistants) == 1
    assert assistants[0]["content"] == "Hello world"


def test_non_retriable_fails_fast():
    llm = ScriptedLLM(([chunk(content="x")], StreamBroken(400)))
    run, events, _ = drive(Runtime(llm=llm, llm_stream_retry=policy(max_attempts=3)))

    assert run.status is RunStatus.FAILED
    assert llm.calls == 1
    assert not [event for event in events if isinstance(event, ev.LLMStreamRetrying)]


def test_attempts_exhausted_fails():
    broken = ([chunk(content="x")], StreamBroken(503))
    llm = ScriptedLLM(broken, broken, broken)
    run, events, _ = drive(Runtime(llm=llm, llm_stream_retry=policy(max_attempts=3)))

    assert run.status is RunStatus.FAILED
    assert llm.calls == 3
    assert [item.attempt for item in events if isinstance(item, ev.LLMStreamRetrying)] == [1, 2]


def test_no_policy_keeps_old_behavior():
    llm = ScriptedLLM(([chunk(content="H")], StreamBroken(503)))
    run, events, _ = drive(Runtime(llm=llm))

    assert run.status is RunStatus.FAILED
    assert llm.calls == 1
    assert not [event for event in events if isinstance(event, ev.LLMStreamRetrying)]


def test_cancelled_error_never_retried():
    assert policy(max_attempts=3).retriable(asyncio.CancelledError()) is False
