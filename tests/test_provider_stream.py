from flops_agent import ProviderStreamChunk, ReasoningStreamChunk, TextStreamChunk, ToolCallStreamChunk
from flops_agent.engine.runner import Runner
from flops_agent.engine.stream import StreamAccumulator


def test_provider_events_never_become_local_tools():
    acc=StreamAccumulator()
    assert list(acc.feed(ProviderStreamChunk('hosted_tool',{'id':'ws','status':'searching'},True)))==[]
    assert not acc.tool_calls() and not acc.text


def test_only_committed_hosted_event_closes_steer_gate():
    runner=object.__new__(Runner)
    assert not runner.closes_steer_window(ProviderStreamChunk('reasoning',{'encrypted_length':12}))
    assert not runner.closes_steer_window(ReasoningStreamChunk('summary'))
    assert runner.closes_steer_window(ProviderStreamChunk('hosted_tool',{'id':'ws'},True))
    assert runner.closes_steer_window(TextStreamChunk('A'))
    assert runner.closes_steer_window(ToolCallStreamChunk(index=0,id='call'))
