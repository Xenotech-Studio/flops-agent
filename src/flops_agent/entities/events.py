"""Framework agent-loop event vocabulary.

Structured events emitted by the agent loop, one per meaningful thing that
happens inside a step: text/reasoning deltas, tool-call streaming, tool
dispatch + results, and step/loop lifecycle.  These are the framework → product
contract that replaces the raw ``f"data: {json.dumps(...)}\\n\\n"`` SSE strings
that used to be yielded inline from ``chat_stream``.

The framework knows nothing about SSE.  The product owns the wire mapping
(``server.py``'s ``to_sse``), so the same events can drive SSE today and a
different transport (WebSocket, a plain callback, a test harness) later.

Design notes:
- ``ProductEvent`` is the escape hatch.  Product-specific frames that have no
  generic meaning to the framework (title suggestion, timing telemetry, safety
  review, usage, layout hints, …) ride through as an opaque payload so the
  product can interleave them in-order with framework events.
- These are plain data.  No behavior, no I/O, no serialization here.

Extracted from server.py chat_v2 generate_response() — step 1 of the
AgentLoop framework extraction (pure transport reformat, no behavior change).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


def _empty_dict() -> Dict[str, Any]:
    return {}

# ──────────────────────────────────────────────────────────────────────────────
#  Streaming: text + reasoning
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class TextDelta:
    """A chunk of visible assistant text."""

    text: str


@dataclass
class ReasoningDelta:
    """A reasoning/thinking signal.

    ``phase="start"`` marks the beginning of a thinking segment (no text);
    ``phase="delta"`` carries an incremental chunk of reasoning text.
    """

    text: Optional[str] = None
    phase: str = "delta"  # "start" | "delta"


# ──────────────────────────────────────────────────────────────────────────────
#  Streaming: tool-call assembly
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class ToolCallStarted:
    """A new tool call began streaming (first fragment carrying its name)."""

    index: int
    name: str


@dataclass
class ToolCallArgsDelta:
    """An incremental fragment of a tool call's argument JSON."""

    index: int
    args_delta: str


@dataclass
class ToolCallReady:
    """A tool call is fully assembled and about to be dispatched."""

    index: int
    name: str
    arguments: str


# ──────────────────────────────────────────────────────────────────────────────
#  Tool dispatch + results
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class ToolExecuting:
    """Dispatch of the tool at ``index`` has begun."""

    index: int


@dataclass
class ToolResultDelta:
    """A streaming fragment of a tool's result (stdout append / patch / set)."""

    index: int
    delta: Dict[str, Any]


@dataclass
class ToolResult:
    """Final result of a tool call.

    ``ok`` reflects success (no error in the result); the product decides how
    to surface it on the wire.
    """

    index: int
    name: str
    result: Any
    ok: bool = True


# ──────────────────────────────────────────────────────────────────────────────
#  Step / loop lifecycle
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class StepCompleted:
    """One agent step finished (LLM turn + any tool dispatch)."""

    step: int = -1


@dataclass
class LoopFinished:
    """The agent loop terminated normally (LLM returned no tool calls)."""

    reason: str = "stop"
    #: Product-supplied count of inputs still pending after this run. The
    #: framework does not prescribe how those inputs are queued or delivered.
    pending_input_count: Any = None


@dataclass
class Suspended:
    """The loop paused mid-tool awaiting a user decision (sensitive review /
    ask_user_question).  ``marker`` carries the product's resume payload."""

    marker: Dict[str, Any] = field(default_factory=_empty_dict)


@dataclass
class InteractionRequested:
    """A tool call requires a durable human response before suspension.

    ``tool_call_id`` lets a client associate its answer with this call.
    """

    kind: str
    index: int
    tool_name: str
    tool_call_id: str
    payload: Dict[str, Any] = field(default_factory=_empty_dict)


@dataclass
class InteractionResolved:
    """A resumed answer was injected into history as this tool's result."""

    kind: str
    tool_name: str
    tool_call_id: str
    result: Any = None


@dataclass
class Cancelled:
    """The run was cancelled (client abort / worker cancel)."""


@dataclass
class Error:
    """A terminal error ended the run."""

    message: str
    exc: Optional[BaseException] = None


@dataclass
class LLMStreamRetrying:
    """A streamed LLM step failed and will be retried after backoff.

    Retry discards partial output rather than resuming it; consumers should
    reset their partial state before the replacement step begins.
    """

    attempt: int
    max_attempts: int
    backoff_s: float
    partial_len: int = 0
    exc_type: str = ""


# ──────────────────────────────────────────────────────────────────────────────
#  Escape hatch
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class HistoryChanged:
    """Session history was persisted at the given revision.

    Consumers can reload when event-stream rendering may have diverged from
    persisted history.
    """

    revision: int
    message_count: int = 0


@dataclass
class ProductEvent:
    """A product-specific frame with no generic framework meaning.

    ``payload`` is the exact object to emit (already includes its own ``type``
    field where applicable); ``kind`` is an informational tag for filtering.
    ``ensure_ascii`` preserves the product's original serialization choice.
    """

    kind: str
    payload: Dict[str, Any]
    ensure_ascii: bool = True


# All framework event types (excludes the ProductEvent escape hatch).
AgentEvent = (
    TextDelta
    | ReasoningDelta
    | ToolCallStarted
    | ToolCallArgsDelta
    | ToolCallReady
    | ToolExecuting
    | ToolResultDelta
    | ToolResult
    | StepCompleted
    | LoopFinished
    | Suspended
    | InteractionRequested
    | InteractionResolved
    | Cancelled
    | Error
    | LLMStreamRetrying
    | HistoryChanged
    | ProductEvent
)


__all__ = [
    "TextDelta",
    "ReasoningDelta",
    "ToolCallStarted",
    "ToolCallArgsDelta",
    "ToolCallReady",
    "ToolExecuting",
    "ToolResultDelta",
    "ToolResult",
    "StepCompleted",
    "LoopFinished",
    "Suspended",
    "InteractionRequested",
    "InteractionResolved",
    "Cancelled",
    "Error",
    "LLMStreamRetrying",
    "HistoryChanged",
    "ProductEvent",
    "AgentEvent",
]
