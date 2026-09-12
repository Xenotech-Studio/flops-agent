"""A starter LLM client: supply a DeepSeek key and run.

The framework requires only ``LLMStreamClient``. This ready-to-use client works
with any OpenAI-compatible endpoint, including DeepSeek, OpenAI, local vLLM,
and OpenRouter; set ``base_url`` and ``model`` accordingly. It depends on the
optional ``httpx`` extra. Production deployments often implement the seam with
their own provider routing; ``Sample`` deliberately means the shortest useful
path, not the only production design.
"""
from __future__ import annotations

import json
from types import SimpleNamespace as _NS
from typing import Any, AsyncIterator, Dict, List, cast

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore


def _as_dict(x: Any) -> Dict[str, Any]:
    """Return a dict leniently; missing stream fields become an empty mapping."""
    return cast(Dict[str, Any], x) if isinstance(x, dict) else {}


def _chunk_from_json(obj: Dict[str, Any]) -> Any:
    """Convert an OpenAI JSON stream chunk into the nested accumulator shape."""
    choices: List[Any] = []
    for ch in cast(List[Dict[str, Any]], obj.get("choices") or []):
        d = _as_dict(ch.get("delta"))
        tcs = None
        if d.get("tool_calls"):
            tcs = [
                _NS(
                    index=tc.get("index", 0),
                    id=tc.get("id"),
                    type=tc.get("type", "function"),
                    function=_NS(
                        name=_as_dict(tc.get("function")).get("name"),
                        arguments=_as_dict(tc.get("function")).get("arguments"),
                    ),
                )
                for tc in cast(List[Dict[str, Any]], d["tool_calls"])
            ]
        choices.append(_NS(
            index=ch.get("index", 0),
            finish_reason=ch.get("finish_reason"),
            delta=_NS(
                content=d.get("content"),
                reasoning_content=d.get("reasoning_content"),
                tool_calls=tcs,
            ),
        ))
    return _NS(choices=choices, usage=obj.get("usage"))


class SampleDeepseekClient:
    """Starter streaming LLM client using DeepSeek's OpenAI-compatible protocol.

        deepseek = SampleDeepseekClient(api_key="sk-...")
        runtime = Runtime(llm=deepseek, database=...)

    Args:
        api_key: Provider API key.
        model: Default model; a request-level ``model`` takes precedence.
        base_url: OpenAI-compatible endpoint, DeepSeek by default.
        timeout: Per-request timeout in seconds.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com",
        timeout: float = 120.0,
    ):
        if httpx is None:
            raise RuntimeError(
                "SampleDeepseekClient requires httpx: pip install \"flops-agent[providers]\""
            )
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def acompletion(self, **request: Any) -> AsyncIterator[Any]:
        """Start a streaming completion and return an async iterable of chunks.

        ``CancelledError`` propagates unchanged; cancellation cleanup belongs
        to the caller and httpx closes its stream when iteration is cancelled.
        """
        body: Dict[str, Any] = {k: v for k, v in request.items() if v is not None}
        body.setdefault("model", self.model)
        body["stream"] = True
        # Request usage on the final frame for framework accounting.
        body.setdefault("stream_options", {"include_usage": True})
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/chat/completions"

        async def _gen() -> AsyncIterator[Any]:
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
                        yield _chunk_from_json(obj)

        return _gen()


__all__ = ["SampleDeepseekClient"]
