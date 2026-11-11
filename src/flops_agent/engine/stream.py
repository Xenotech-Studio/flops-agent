"""Accumulate provider-neutral stream chunks into events and step state.

``StreamAccumulator.feed(chunk)`` emits text, reasoning, and tool-call events
while assembling complete text, reasoning, and indexed tool calls. After the
stream ends, ``tool_calls()`` returns OpenAI-shaped call objects.
"""
from __future__ import annotations

from collections.abc import Iterator

from flops_agent.entities import events as _ev
from flops_agent.entities.contracts import (
    FinishStreamChunk,
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
        """Consume one public :data:`StreamChunk`."""
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
        # Only FinishStreamChunk is left statically, so the check below is a
        # runtime guard, not a type narrowing: annotations do not stop a custom
        # client from yielding a provider SDK object. Say so loudly instead of
        # dropping the model's output on the floor.
        if not isinstance(chunk, FinishStreamChunk):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError(
                "LLMStreamClient.acompletion must yield StreamChunk values "
                "(TextStreamChunk / ReasoningStreamChunk / ToolCallStreamChunk / "
                f"FinishStreamChunk); got {type(chunk).__module__}.{type(chunk).__name__}. "
                "Provider wire formats are translated inside the client -- see "
                "flops_agent.providers.openai.OpenAIStreamClient."
            )
        # FinishStreamChunk carries terminal telemetry only (finish_reason /
        # usage, read by Runner.on_chunk); it produces no events.

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

    def tool_calls(self) -> list[ToolCall]:
        return [slot for _, slot in sorted(self._tc.items())]


__all__ = ["StreamAccumulator"]
