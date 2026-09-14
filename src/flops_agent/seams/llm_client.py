"""LLM streaming-completion seam for the framework.

The agent loop depends only on ``LLMStreamClient``, not on a particular
provider. Implementations sanitize orphaned tool messages, retry transient
pre-first-token failures, and return an asynchronously iterable stream.
``asyncio.CancelledError`` must propagate unchanged: caller orchestration owns
cancellation cleanup and SSE emission.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from flops_agent.entities.contracts import StreamChunk


@runtime_checkable
class LLMStreamClient(Protocol):
    async def acompletion(self, **completion_kwargs: object) -> AsyncIterator[StreamChunk]:
        """Start one streaming completion and return an async iterable of chunks.

        Implementations sanitize wire data and retry transient failures while
        propagating ``CancelledError`` unchanged.
        """
        ...


__all__ = ["LLMStreamClient"]
