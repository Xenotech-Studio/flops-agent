"""A starter LLM client: supply an OpenAI-compatible key and run.

The framework requires only ``LLMStreamClient``. This ready-to-use client speaks
the OpenAI chat-completions streaming protocol, which also covers DeepSeek,
local vLLM, OpenRouter, and most other OpenAI-compatible endpoints; set
``base_url`` and ``model`` accordingly. It depends on the optional ``httpx``
extra. Production deployments often implement the seam with their own provider
routing; this starter deliberately means the shortest useful path, not the
only production design.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import cast

from flops_agent.entities.contracts import (
    FinishStreamChunk,
    JSONMapping,
    ReasoningStreamChunk,
    StreamChunk,
    TextStreamChunk,
    ToolCallStreamChunk,
)

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore


def _as_dict(x: object) -> dict[str, object]:
    """Return a dict leniently; missing stream fields become an empty mapping."""
    return cast(dict[str, object], x) if isinstance(x, dict) else {}


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _chunks_from_json(obj: dict[str, object]) -> list[StreamChunk]:
    """Normalize one OpenAI-compatible frame into provider-neutral chunks.

    A provider frame may contain text, reasoning, and several tool-call updates
    at once.  Splitting it preserves the accumulator's historical order while
    avoiding the provider's nested ``choices[].delta`` shape in our contract.
    """
    chunks: list[StreamChunk] = []
    raw_choices = obj.get("choices")
    choices = cast(list[object], raw_choices) if isinstance(raw_choices, list) else []
    for raw_choice in choices[:1]:
        ch = _as_dict(raw_choice)
        d = _as_dict(ch.get("delta"))
        content = _string(d.get("content"))
        if content:
            chunks.append(TextStreamChunk(content))
        reasoning = _string(d.get("reasoning_content"))
        if reasoning:
            chunks.append(ReasoningStreamChunk(reasoning))
        raw_calls = d.get("tool_calls")
        calls = cast(list[object], raw_calls) if isinstance(raw_calls, list) else []
        for raw_call in calls:
            call = _as_dict(raw_call)
            raw_index = call.get("index")
            index = raw_index if isinstance(raw_index, int) else 0
            function = _as_dict(call.get("function"))
            chunks.append(ToolCallStreamChunk(
                index=index,
                id=_string(call.get("id")),
                name=_string(function.get("name")),
                arguments_delta=_string(function.get("arguments")),
            ))

    first_choice = _as_dict(choices[0]) if choices else {}
    reason = _string(first_choice.get("finish_reason"))
    raw_usage = obj.get("usage")
    usage = cast(JSONMapping, raw_usage) if isinstance(raw_usage, dict) else None
    if reason is not None or usage is not None:
        chunks.append(FinishStreamChunk(reason=reason, usage=usage))
    return chunks


class OpenAIStreamClient:
    """Starter streaming LLM client using the OpenAI chat-completions protocol.

        llm = OpenAIStreamClient(api_key="sk-...", model="gpt-4o-mini")
        runtime = Runtime(llm=llm, database=...)

    Point ``base_url`` at any other OpenAI-compatible endpoint (DeepSeek,
    OpenRouter, local vLLM, ...) to reuse the same client there.

    Args:
        api_key: Provider API key.
        model: Default model; a request-level ``model`` takes precedence.
        base_url: OpenAI-compatible endpoint, OpenAI's by default.
        timeout: Per-request timeout in seconds.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 120.0,
    ):
        if httpx is None:
            raise RuntimeError(
                "OpenAIStreamClient requires httpx: pip install \"flops-agent[providers]\""
            )
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def acompletion(self, **request: object) -> AsyncIterator[StreamChunk]:
        """Start a streaming completion and return an async iterable of chunks.

        ``CancelledError`` propagates unchanged; cancellation cleanup belongs
        to the caller and httpx closes its stream when iteration is cancelled.
        """
        body: dict[str, object] = {k: v for k, v in request.items() if v is not None}
        body.setdefault("model", self.model)
        body["stream"] = True
        # Request usage on the final frame for framework accounting.
        body.setdefault("stream_options", {"include_usage": True})
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/chat/completions"

        async def _gen() -> AsyncIterator[StreamChunk]:
            assert httpx is not None  # Guaranteed by __init__; retained for type checking.
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", url, headers=headers, json=body) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[len("data:"):].strip()
                        if payload == "[DONE]":
                            return
                        try:
                            obj = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(obj, dict):
                            continue
                        for chunk in _chunks_from_json(cast(dict[str, object], obj)):
                            yield chunk

        return _gen()


__all__ = ["OpenAIStreamClient"]
