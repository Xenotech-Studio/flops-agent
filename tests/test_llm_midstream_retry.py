"""Framework contract for automatically retrying an LLM stream that fails mid-flight."""

import asyncio

from flops_agent import Query, RunStatus, Runtime, Session
from flops_agent.engine.runtime import LLMStreamRetryPolicy
from flops_agent.entities import events as ev
from conftest import text_chunk  # noqa: E402




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
        ([text_chunk("H")], StreamBroken(503)),
        ([text_chunk("Hello"), text_chunk(" world")], None),
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
    llm = ScriptedLLM(([text_chunk("x")], StreamBroken(400)))
    run, events, _ = drive(Runtime(llm=llm, llm_stream_retry=policy(max_attempts=3)))

    assert run.status is RunStatus.FAILED
    assert llm.calls == 1
    assert not [event for event in events if isinstance(event, ev.LLMStreamRetrying)]


def test_product_can_repair_and_rebuild_one_failed_request_before_retry_policy():
    from flops_agent import Runner

    class Recovering(Runner):
        recoveries = 0

        async def recover_llm_failure(self, exc, acc, attempt):
            assert isinstance(exc, StreamBroken)
            assert not acc.text and not acc.tool_calls()
            assert attempt == 1
            type(self).recoveries += 1
            return {"messages": [{"role": "user", "content": "compacted"}], "stream": True}

    llm = ScriptedLLM(
        ([], StreamBroken(400)),
        ([text_chunk("ok")], None),
    )
    run, events, session = drive(Runtime(llm=llm, runner=Recovering))

    assert run.status is RunStatus.DONE
    assert llm.calls == 2
    assert Recovering.recoveries == 1
    assert not [event for event in events if isinstance(event, ev.LLMStreamRetrying)]
    assert [m["content"] for m in session.messages if m.get("role") == "assistant"] == ["ok"]


def test_product_failure_recovery_is_framework_bounded_to_one_replacement():
    from flops_agent import Runner

    class OvereagerRecovery(Runner):
        recoveries = 0

        async def recover_llm_failure(self, exc, acc, attempt):
            type(self).recoveries += 1
            return {"messages": [], "stream": True}

    llm = ScriptedLLM(([], StreamBroken(400)), ([], StreamBroken(400)))
    run, _events, _session = drive(Runtime(llm=llm, runner=OvereagerRecovery))

    assert run.status is RunStatus.FAILED
    assert llm.calls == 2
    assert OvereagerRecovery.recoveries == 1


def test_attempts_exhausted_fails():
    broken = ([text_chunk("x")], StreamBroken(503))
    llm = ScriptedLLM(broken, broken, broken)
    run, events, _ = drive(Runtime(llm=llm, llm_stream_retry=policy(max_attempts=3)))

    assert run.status is RunStatus.FAILED
    assert llm.calls == 3
    assert [item.attempt for item in events if isinstance(item, ev.LLMStreamRetrying)] == [1, 2]


def test_no_policy_keeps_old_behavior():
    llm = ScriptedLLM(([text_chunk("H")], StreamBroken(503)))
    run, events, _ = drive(Runtime(llm=llm))

    assert run.status is RunStatus.FAILED
    assert llm.calls == 1
    assert not [event for event in events if isinstance(event, ev.LLMStreamRetrying)]


def test_cancelled_error_never_retried():
    assert policy(max_attempts=3).retriable(asyncio.CancelledError()) is False


def test_completion_validation_is_inside_existing_retry_after_stream_close():
    from flops_agent import Runner
    closed=[]
    class LLM(ScriptedLLM):
        async def acompletion(self, **kwargs):
            stream=await super().acompletion(**kwargs)
            number=self.calls
            async def owned():
                try:
                    async for chunk in stream:yield chunk
                finally:closed.append(number)
            return owned()
    class Validating(Runner):
        async def validate_stream_completion(self, acc):
            assert closed[-1]==self.runtime.llm.calls
            if not acc.text:raise StreamBroken(503)
    llm=LLM(([],None),([text_chunk('ok')],None))
    run,events,session=drive(Runtime(llm=llm,runner=Validating,llm_stream_retry=policy()))
    assert run.status is RunStatus.DONE and llm.calls==2 and closed==[1,2]
    assert [m['content'] for m in session.messages if m.get('role')=='assistant']==['ok']


def test_post_completion_hook_failure_cannot_replay_successful_output():
    from flops_agent import Runner
    class AccountingFailure(Runner):
        async def on_stream_end(self):raise StreamBroken(503)
    llm=ScriptedLLM(([text_chunk('already emitted')],None))
    run,events,_=drive(Runtime(llm=llm,runner=AccountingFailure,llm_stream_retry=policy()))
    assert run.status is RunStatus.FAILED and llm.calls==1
    assert not [e for e in events if isinstance(e,ev.LLMStreamRetrying)]


def test_retry_does_not_inherit_previous_attempt_usage():
    from flops_agent import Runner, FinishStreamChunk
    seen=[]
    class Observe(Runner):
        async def on_stream_end(self):seen.append(self.usage)
    llm=ScriptedLLM(([FinishStreamChunk(usage={'prompt_tokens':123})],StreamBroken(503)),([text_chunk('ok')],None))
    run,_,_=drive(Runtime(llm=llm,runner=Observe,llm_stream_retry=policy()))
    assert run.status is RunStatus.DONE and llm.calls==2 and seen==[None]
