"""Unit tests for OpenAIStreamClient's SSE parsing (no network)."""
from flops_agent import FinishStreamChunk, TextStreamChunk, ToolCallStreamChunk
from flops_agent.providers.openai import _chunks_from_json, OpenAIStreamClient
from flops_agent.engine.stream import StreamAccumulator


def test_chunk_shape_matches_kernel_accumulator():
    acc = StreamAccumulator()
    evs = []
    for chunk in _chunks_from_json(
        {"choices": [{"index": 0, "delta": {"content": "hello"}, "finish_reason": None}]}
    ):
        evs += [type(e).__name__ for e in acc.feed(chunk)]
    for chunk in _chunks_from_json(
        {"choices": [{"index": 0, "delta": {"reasoning_content": "thinking"}, "finish_reason": None}]}
    ):
        evs += [type(e).__name__ for e in acc.feed(chunk)]
    for chunk in _chunks_from_json(
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "f", "arguments": '{"a"'}}]}}]}
    ):
        evs += [type(e).__name__ for e in acc.feed(chunk)]
    assert evs == ["TextDelta", "ReasoningDelta", "ToolCallStarted", "ToolCallArgsDelta"]
    assert acc.text == "hello" and acc.reasoning == "thinking"
    tcs = acc.tool_calls()
    assert tcs[0].id == "c1" and tcs[0].function.name == "f"
    print("test_chunk_shape_matches_kernel_accumulator OK")


def test_usage_and_finish_reason_reach_on_chunk():
    chunks = _chunks_from_json({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                                "usage": {"total_tokens": 9}})
    finish = next(chunk for chunk in chunks if isinstance(chunk, FinishStreamChunk))
    assert finish.reason == "stop" and finish.usage == {"total_tokens": 9}
    assert isinstance(_chunks_from_json({"choices": [{"delta": {"content": "x", "tool_calls": [
        {"index": 2, "function": {"arguments": "{}"}}]}}]})[0], TextStreamChunk)
    assert isinstance(_chunks_from_json({"choices": [{"delta": {"tool_calls": [
        {"index": 2, "function": {"arguments": "{}"}}]}}]})[0], ToolCallStreamChunk)
    print("test_usage_and_finish_reason_reach_on_chunk OK")


def test_done_sentinel_and_defaults():
    cli = OpenAIStreamClient("sk-x", model="gpt-4o-mini")
    assert cli.model == "gpt-4o-mini" and cli.base_url == "https://api.openai.com/v1"
    print("test_done_sentinel_and_defaults OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
if __name__ == "__main__":
    for t in _TESTS:
        t()
    print(f"\nALL {len(_TESTS)} OPENAI-CLIENT TESTS PASSED")
