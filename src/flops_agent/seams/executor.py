"""Framework tool-executor seam.

The agent loop dispatches a tool call through a ``ToolExecutor``:

    result = await executor.execute(call, ctx)

``ctx`` is the protocol-level ``ToolContext`` (request-dimension data only:
user/session/tool_domains/stream_sink/…). The framework does not know how
a tool actually runs — the executor owns that.

Two implementations:

* ``DefaultToolExecutor`` — zero-injection default.  Parses the call's
  arguments and runs it straight through the framework dispatch seam
  (``dispatch_tool``: package gate → route → invoke).  A third-party developer
  gets a working agent with this and nothing else.
* The product (Flops) injects its own executor that wraps ``server.execute_tool``
  (which adds tool-name compat, tool_domains resolution, redis injection, ctx
  construction, executor routing, sensitive-command scanning, streaming relay,
  … before calling the same ``dispatch_tool``).

This establishes the seam; the product
executor still wraps the existing ``execute_tool`` unchanged.
"""
from __future__ import annotations

import logging
from typing import Optional, Protocol, runtime_checkable

from flops_agent.entities.contracts import ToolCall, ToolOutcome
from flops_agent.tools.registry import ToolContext, ToolRouter
from flops_agent.tools.dispatch import dispatch_tool
from flops_agent.tools.schema import parse_tool_arguments

logger = logging.getLogger(__name__)


def _has_error(value: object) -> bool:
    return isinstance(value, dict) and "error" in value


@runtime_checkable
class ToolExecutor(Protocol):
    """Runs one assembled tool call and returns its result object."""

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolOutcome:
        ...


class DefaultToolExecutor:
    """Zero-injection executor: dispatch the call through the framework registry.

    ``call`` is the provider-neutral :class:`ToolCall`. ``ctx.function_name`` and
    ``ctx.tool_domains`` drive dispatch; an optional ``router`` decides
    executor-routed tools (``None`` → everything runs against the registry).
    """

    def __init__(self, *, router: Optional[ToolRouter] = None):
        self._router = router

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolOutcome:
        parsed = parse_tool_arguments(call.function.arguments)
        if not parsed.ok:
            # Do not silently turn malformed arguments into an empty dict: that
            # may run the tool with a more dangerous “not provided” meaning.
            # Return the original failure category so the model can retry.
            logger.warning(
                "tool %s: bad arguments (%s): %s", ctx.function_name, parsed.fail_kind, parsed.error,
            )
            return ToolOutcome({
                "success": False,
                "error": f"Tool arguments are not valid JSON ({parsed.fail_kind}): {parsed.error}",
                "tool_name": ctx.function_name,
                "arguments_fail_kind": parsed.fail_kind,
            }, ok=False)
        result = await dispatch_tool(call, parsed.arguments, ctx, router=self._router)
        if isinstance(result, ToolOutcome):
            return result
        return ToolOutcome(result, ok=not _has_error(result))


__all__ = ["ToolExecutor", "DefaultToolExecutor", "ToolContext"]
