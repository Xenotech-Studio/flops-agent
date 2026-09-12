"""Accumulate OpenAI/LiteLLM-style chunks into events and step state.

``StreamAccumulator.feed(chunk)`` emits text, reasoning, and tool-call events
while assembling complete text, reasoning, and indexed tool calls. After the
stream ends, ``tool_calls()`` returns OpenAI-shaped call objects.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

from flops_agent.entities import events as _ev


class StreamAccumulator:
    """Turns an OpenAI/LiteLLM chunk stream into events + assembled state.

    ``feed(chunk)`` yields ``TextDelta`` / ``ReasoningDelta`` / ``ToolCallStarted``
    / ``ToolCallArgsDelta`` as fragments arrive, accumulating full text,
    reasoning, and per-index tool calls.
    """

    def __init__(self) -> None:
        self.text = ""
        self.reasoning = ""
        self._tc: Dict[int, Dict[str, Any]] = {}
        self._named: set[int] = set()

    def feed(self, chunk: Any):
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
            slot = self._tc.setdefault(
                idx, {"id": None, "type": "function", "name": "", "arguments": ""}
            )
            if getattr(tc, "id", None):
                slot["id"] = tc.id
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            name = getattr(fn, "name", None)
            if name:
                slot["name"] += name
                if idx not in self._named:
                    self._named.add(idx)
                    yield _ev.ToolCallStarted(idx, slot["name"])
            args = getattr(fn, "arguments", None)
            if args:
                slot["arguments"] += args
                yield _ev.ToolCallArgsDelta(idx, args)

    def tool_calls(self) -> List[Any]:
        return [
            SimpleNamespace(
                id=slot["id"],
                type=slot["type"],
                function=SimpleNamespace(name=slot["name"], arguments=slot["arguments"]),
            )
            for _, slot in sorted(self._tc.items())
        ]


__all__ = ["StreamAccumulator"]
