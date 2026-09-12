"""Wire serialization for framework events.

``WireCodec`` is the production-ready default for the ``runtime.wire``
injection point. It maps framework events to stable snake-case wire types,
injects reconnect cursors into live deliveries, and passes pre-serialized SSE
strings through unchanged. Module-level helpers delegate to the default codec.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Tuple, Type, Union, cast

from flops_agent.entities import events as _ev

#: Canonical framework-event to wire-type table; clients branch on these values.
WIRE_TYPES: Dict[Type[Any], str] = {
    _ev.TextDelta: "text_delta",
    _ev.ReasoningDelta: "reasoning_delta",
    _ev.ToolCallStarted: "tool_call_started",
    _ev.ToolCallArgsDelta: "tool_call_args_delta",
    _ev.ToolCallReady: "tool_call_ready",
    _ev.ToolExecuting: "tool_executing",
    _ev.ToolResultDelta: "tool_result_delta",
    _ev.ToolResult: "tool_result",
    _ev.StepCompleted: "step_completed",
    _ev.LoopFinished: "loop_finished",
    _ev.Suspended: "suspended",
    _ev.InteractionRequested: "interaction_requested",
    _ev.InteractionResolved: "interaction_resolved",
    _ev.Cancelled: "cancelled",
    _ev.Error: "error",
    _ev.LLMStreamRetrying: "llm_retrying",
    _ev.HistoryChanged: "history_changed",
}


def _jsonable(value: Any) -> Any:
    """Best-effort JSON conversion, falling back to ``str`` for unknown objects."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in cast(Dict[Any, Any], value).items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in cast(Union[List[Any], Tuple[Any, ...]], value)]
    return str(value)


class WireCodec:
    """Production-ready default serializer for ``runtime.wire``.

    Emit ``ProductEvent`` for a custom frame, subclass :meth:`event_to_wire`
    to reshape selected events, or inject a codec implementing the same methods
    for an entirely different protocol.

    Args:
        ensure_ascii: JSON escaping policy; a ``ProductEvent`` setting wins.
        types: Optional override for the event type table.
    """

    def __init__(
        self,
        *,
        ensure_ascii: bool = False,
        types: Optional[Mapping[Type[Any], str]] = None,
    ) -> None:
        self.ensure_ascii = ensure_ascii
        self.types: Mapping[Type[Any], str] = types if types is not None else WIRE_TYPES

    # ── Event to wire dictionary ─────────────────────────────────────────────

    def event_to_wire(self, event: Any) -> Optional[Dict[str, Any]]:
        """Convert a framework event to a wire dictionary.

        ``ProductEvent`` passes through its payload. Pre-serialized strings and
        unknown objects return ``None``; :meth:`to_sse` passes the former through
        and represents the latter as ``unknown_event``.
        """
        if isinstance(event, str):
            return None
        if isinstance(event, _ev.ProductEvent):
            out = {"type": event.kind}
            out.update(_jsonable(event.payload) or {})
            return out
        wire_type = self.types.get(cast(Type[Any], type(event)))
        if wire_type is None:
            return None
        out: Dict[str, Any] = {"type": wire_type}
        if isinstance(event, _ev.TextDelta):
            out["text"] = event.text
        elif isinstance(event, _ev.ReasoningDelta):
            out["text"] = event.text
            out["phase"] = event.phase
        elif isinstance(event, _ev.ToolCallStarted):
            out["index"] = event.index
            out["name"] = event.name
        elif isinstance(event, _ev.ToolCallArgsDelta):
            out["index"] = event.index
            out["args_delta"] = event.args_delta
        elif isinstance(event, _ev.ToolCallReady):
            out["index"] = event.index
            out["name"] = event.name
            out["arguments"] = event.arguments
        elif isinstance(event, _ev.ToolExecuting):
            out["index"] = event.index
        elif isinstance(event, _ev.ToolResultDelta):
            out["index"] = event.index
            out["delta"] = _jsonable(event.delta)
        elif isinstance(event, _ev.ToolResult):
            out["index"] = event.index
            out["name"] = event.name
            out["result"] = _jsonable(event.result)
            out["ok"] = event.ok
        elif isinstance(event, _ev.StepCompleted):
            out["step"] = event.step
        elif isinstance(event, _ev.LoopFinished):
            out["reason"] = event.reason
            if event.send_queue_pending is not None:
                out["send_queue_pending"] = event.send_queue_pending
            out["done"] = True    # Conventional terminal flag for clients.
        elif isinstance(event, _ev.InteractionRequested):
            out["kind"] = event.kind
            out["index"] = event.index
            out["tool_name"] = event.tool_name
            out["tool_call_id"] = event.tool_call_id
            out["payload"] = _jsonable(event.payload)
        elif isinstance(event, _ev.InteractionResolved):
            out["kind"] = event.kind
            out["tool_name"] = event.tool_name
            out["tool_call_id"] = event.tool_call_id
            out["result"] = _jsonable(event.result)
        elif isinstance(event, _ev.Suspended):
            out["marker"] = _jsonable(event.marker)
            out["done"] = True
        elif isinstance(event, _ev.Cancelled):
            out["done"] = True
        elif isinstance(event, _ev.Error):
            out["message"] = event.message   # Exceptions and stacks remain server-side.
        elif isinstance(event, _ev.LLMStreamRetrying):
            # A mid-stream failure retries the whole step; expose partial length.
            out["attempt"] = event.attempt
            out["max_attempts"] = event.max_attempts
            out["backoff_s"] = event.backoff_s
            out["partial_len"] = event.partial_len
            out["exc_type"] = event.exc_type
        elif isinstance(event, _ev.HistoryChanged):
            out["revision"] = event.revision
            out["message_count"] = event.message_count
        return out

    # ── Event/delivery to SSE line ───────────────────────────────────────────

    def _dumps(self, wire: Dict[str, Any], event: Any) -> str:
        ensure_ascii = self.ensure_ascii
        if isinstance(event, _ev.ProductEvent):
            ensure_ascii = event.ensure_ascii
        return "data: " + json.dumps(wire, ensure_ascii=ensure_ascii) + "\n\n"

    def to_sse(self, event: Any) -> str:
        """Convert an event to an SSE line, passing strings through unchanged.

        Unknown objects become ``unknown_event`` rather than being dropped.
        """
        if isinstance(event, str):
            return event
        wire = self.event_to_wire(event)
        if wire is None:
            wire = {"type": "unknown_event", "repr": str(event)[:200]}
        return self._dumps(wire, event)

    def delivery_to_sse(self, delivery: Any) -> str:
        """Convert a delivery to SSE, injecting its cursor for live segments.

        Replayed deliveries and pre-serialized strings pass through unchanged.
        """
        event = getattr(delivery, "event", delivery)
        if isinstance(event, str) or getattr(delivery, "replayed", False):
            return self.to_sse(event)
        wire = self.event_to_wire(event)
        if wire is None:
            wire = {"type": "unknown_event", "repr": str(event)[:200]}
        cursor = getattr(delivery, "cursor", None)
        if cursor is not None:
            wire["cursor"] = cursor
        return self._dumps(wire, event)


#: Shared default codec for helpers and unconfigured runtimes.
DEFAULT_CODEC = WireCodec()


def event_to_wire(event: Any) -> Optional[Dict[str, Any]]:
    """Convenience wrapper for :data:`DEFAULT_CODEC`."""
    return DEFAULT_CODEC.event_to_wire(event)


def to_sse(event: Any, *, ensure_ascii: bool = False) -> str:
    """Convenience wrapper for the standard codec."""
    codec = DEFAULT_CODEC if not ensure_ascii else WireCodec(ensure_ascii=True)
    return codec.to_sse(event)


def delivery_to_sse(delivery: Any, *, ensure_ascii: bool = False) -> str:
    """Convenience wrapper for the standard codec."""
    codec = DEFAULT_CODEC if not ensure_ascii else WireCodec(ensure_ascii=True)
    return codec.delivery_to_sse(delivery)


__all__ = [
    "WIRE_TYPES", "WireCodec", "DEFAULT_CODEC",
    "event_to_wire", "to_sse", "delivery_to_sse",
]
