from flops_agent import ProviderStreamChunk
from flops_agent.engine.stream import StreamAccumulator


def test_provider_events_never_become_local_tools():
    acc=StreamAccumulator()
    assert list(acc.feed(ProviderStreamChunk('hosted_tool',{'id':'ws','status':'searching'},True)))==[]
    assert not acc.tool_calls() and not acc.text
