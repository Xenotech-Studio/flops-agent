"""Provider-neutral contracts at the framework's extension boundaries.

The classes in this module deliberately describe the values that the agent loop
consumes, rather than a particular provider SDK's response objects.  This keeps
OpenAI-compatible wire details (including DeepSeek's) outside the public API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias, TypedDict


JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
JSONMapping: TypeAlias = dict[str, JSONValue]
ToolArguments: TypeAlias = dict[str, object]
DispatchRecord: TypeAlias = dict[str, object]


@dataclass
class ToolFunction:
    """The function component of a provider-neutral :class:`ToolCall`."""

    name: str = ""
    arguments: str = "{}"


@dataclass
class ToolCall:
    """A fully assembled function call ready for tool dispatch.

    Its attributes intentionally match the OpenAI/LiteLLM duck shape
    (``id``, ``type``, and ``function.name`` / ``function.arguments``), so
    existing producers and consumers retain their runtime behavior.
    """

    id: str | None = None
    function: ToolFunction = field(default_factory=ToolFunction)
    type: Literal["function"] = "function"


class OpenAIToolCall(TypedDict):
    """The persisted OpenAI-compatible representation of :class:`ToolCall`."""

    id: str | None
    type: Literal["function"]
    function: dict[str, str]


@dataclass(frozen=True)
class TextStreamChunk:
    """One visible-text delta from an LLM stream."""

    text: str
    kind: Literal["text"] = "text"


@dataclass(frozen=True)
class ReasoningStreamChunk:
    """One reasoning-channel delta from an LLM stream."""

    text: str
    kind: Literal["reasoning"] = "reasoning"


@dataclass(frozen=True)
class ToolCallStreamChunk:
    """One incremental update to a tool call.

    Providers may supply an id, name, and argument fragment in the same chunk.
    The accumulator preserves the established event ordering: name starts a
    call before its argument delta is emitted.
    """

    index: int
    id: str | None = None
    name: str | None = None
    arguments_delta: str | None = None
    kind: Literal["tool_call"] = "tool_call"


@dataclass(frozen=True)
class FinishStreamChunk:
    """The stream's terminal provider frame, including complete usage data."""

    reason: str | None = None
    usage: JSONMapping | None = None
    kind: Literal["finish"] = "finish"


StreamChunk: TypeAlias = (
    TextStreamChunk
    | ReasoningStreamChunk
    | ToolCallStreamChunk
    | FinishStreamChunk
)


@dataclass
class ToolOutcome:
    """The typed result returned by a :class:`~flops_agent.ToolExecutor`.

    ``value`` is deliberately an ``object``: the framework preserves a tool's
    result byte-for-byte in history and display events, while ``ok`` makes its
    transport-independent execution status explicit.
    """

    value: object
    ok: bool = True


__all__ = [
    "JSONScalar",
    "JSONValue",
    "JSONMapping",
    "ToolArguments",
    "DispatchRecord",
    "ToolFunction",
    "ToolCall",
    "OpenAIToolCall",
    "TextStreamChunk",
    "ReasoningStreamChunk",
    "ToolCallStreamChunk",
    "FinishStreamChunk",
    "StreamChunk",
    "ToolOutcome",
]
