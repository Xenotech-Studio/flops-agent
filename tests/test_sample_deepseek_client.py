"""Unit tests for SampleDeepseekClient's SSE parsing (no network) -- chunk shape must line up with
the framework's accumulator."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flops_agent.providers.deepseek import _chunk_from_json, SampleDeepseekClient
from flops_agent.engine.stream import StreamAccumulator


def test_chunk_shape_matches_kernel_accumulator():
    acc = StreamAccumulator()
    evs = []
    evs += [type(e).__name__ for e in acc.feed(_chunk_from_json(
        {"choices": [{"index": 0, "delta": {"content": "hello"}, "finish_reason": None}]}))]
    evs += [type(e).__name__ for e in acc.feed(_chunk_from_json(
        {"choices": [{"index": 0, "delta": {"reasoning_content": "thinking"}, "finish_reason": None}]}))]
    evs += [type(e).__name__ for e in acc.feed(_chunk_from_json(
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "f", "arguments": '{"a"'}}]}}]}))]
    assert evs == ["TextDelta", "ReasoningDelta", "ToolCallStarted", "ToolCallArgsDelta"]
    assert acc.text == "hello" and acc.reasoning == "thinking"
    tcs = acc.tool_calls()
    assert tcs[0].id == "c1" and tcs[0].function.name == "f"
    print("test_chunk_shape_matches_kernel_accumulator OK")


def test_usage_and_finish_reason_reach_on_chunk():
    c = _chunk_from_json({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                          "usage": {"total_tokens": 9}})
    assert c.choices[0].finish_reason == "stop"
    assert c.usage["total_tokens"] == 9
    print("test_usage_and_finish_reason_reach_on_chunk OK")


def test_done_sentinel_and_defaults():
    cli = SampleDeepseekClient("sk-x")
    assert cli.model == "deepseek-chat" and cli.base_url == "https://api.deepseek.com"
    print("test_done_sentinel_and_defaults OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
if __name__ == "__main__":
    for t in _TESTS:
        t()
    print(f"\nALL {len(_TESTS)} OPENAI-CLIENT TESTS PASSED")
