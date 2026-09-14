"""Accumulate provider-neutral stream chunks into events and step state.

``StreamAccumulator.feed(chunk)`` emits text, reasoning, and tool-call events
while assembling complete text, reasoning, and indexed tool calls. After the
stream ends, ``tool_calls()`` returns OpenAI-shaped call objects.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import cast

from flops_agent.entities import events as _ev
from flops_agent.entities.contracts import (
    ReasoningStreamChunk,
    StreamChunk,
    TextStreamChunk,
    ToolCall,
    ToolCallStreamChunk,
    ToolFunction,
)


class StreamAccumulator:
    """Turns a typed LLM chunk stream into events + assembled state.

    ``feed(chunk)`` yields ``TextDelta`` / ``ReasoningDelta`` / ``ToolCallStarted``
    / ``ToolCallArgsDelta`` as fragments arrive, accumulating full text,
    reasoning, and per-index tool calls.
    """

    def __init__(self) -> None:
        self.text = ""
        self.reasoning = ""
        self._tc: dict[int, ToolCall] = {}
        self._named: set[int] = set()

    def feed(self, chunk: StreamChunk) -> Iterator[_ev.AgentEvent]:
        """Consume one public :data:`StreamChunk`.

        The fallback preserves source compatibility for pre-contract custom
        clients while the annotated extension boundary requires StreamChunk.
        """
        if isinstance(chunk, TextStreamChunk):
            self.text += chunk.text
            yield _ev.TextDelta(chunk.text)
            return
        if isinstance(chunk, ReasoningStreamChunk):
            self.reasoning += chunk.text
            yield _ev.ReasoningDelta(chunk.text, phase="delta")
            return
        if isinstance(chunk, ToolCallStreamChunk):
            yield from self._feed_tool_call(chunk)
            return
        yield from self._feed_legacy(cast(object, chunk))

    def _feed_tool_call(self, chunk: ToolCallStreamChunk) -> Iterator[_ev.AgentEvent]:
        slot = self._tc.setdefault(chunk.index, ToolCall(function=ToolFunction(arguments="")))
        if chunk.id:
            slot.id = chunk.id
        if chunk.name:
            slot.function.name += chunk.name
            if chunk.index not in self._named:
                self._named.add(chunk.index)
                yield _ev.ToolCallStarted(chunk.index, slot.function.name)
        if chunk.arguments_delta:
            slot.function.arguments += chunk.arguments_delta
            yield _ev.ToolCallArgsDelta(chunk.index, chunk.arguments_delta)

    def _feed_legacy(self, chunk: object) -> Iterator[_ev.AgentEvent]:
        """Adapt the former OpenAI/LiteLLM duck shape during API migration."""
        choices = getattr(chunk, "choices", None)
        if not choices:
            return
        delta = getattr(choices[0], "delta", None)
        if delta is None:
            return

        content = getattr(delta, "content", None)
        if content:
            self.text += content
            yield _ev.TextDelta(content)

        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            self.reasoning += reasoning
            yield _ev.ReasoningDelta(reasoning, phase="delta")

        tcs = getattr(delta, "tool_calls", None)
        if not tcs:
            return
        for tc in tcs:
            idx = getattr(tc, "index", None) or 0
            slot = self._tc.setdefault(idx, ToolCall(function=ToolFunction(arguments="")))
            if getattr(tc, "id", None):
                slot.id = tc.id
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            name = getattr(fn, "name", None)
            if name:
                slot.function.name += name
                if idx not in self._named:
                    self._named.add(idx)
                    yield _ev.ToolCallStarted(idx, slot.function.name)
            args = getattr(fn, "arguments", None)
            if args:
                slot.function.arguments += args
                yield _ev.ToolCallArgsDelta(idx, args)

    def tool_calls(self) -> list[ToolCall]:
        return [slot for _, slot in sorted(self._tc.items())]


__all__ = ["StreamAccumulator"]
