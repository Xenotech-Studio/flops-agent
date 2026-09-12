"""Interaction after tool execution — "stop here and wait for the user to decide".

Some tools do not return a final result; they **need a person to decide**: a choice is
presented or a dangerous command needs confirmation. This turn cannot simply continue,
and has three possible paths:

* **Continue** — there is no interaction requirement (most tools), or the product has
  already replaced the result in place with a terminal result.
* **Wait** — wait in place for the user to respond. Suitable for decisions that take
  seconds: the connection remains open, heartbeat frames keep flowing, then execution resumes.
* **Suspend** — end this turn and record who is expected to answer what. After the user
  responds in **another request**, start another turn and resume. Suitable for long waits.

The product decides between waiting and suspending: waiting saves a round trip but holds
the connection and worker; suspension is more robust but must handle resumption. The
framework only supplies the expression and does not choose for the product.

Why use data rather than exceptions? Control flow stays plainly visible in ``run_step``.
Raising would disguise the normal act of pausing as an error and make it hard to carry
information about what is awaited.
"""
from __future__ import annotations

from typing_extensions import override
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional


def _empty_dict() -> Dict[str, Any]:
    return {}


class InteractionKind(str, Enum):
    CONTINUE = "continue"
    """No interaction is needed; continue with the current result."""

    WAIT = "wait"
    """Wait in place for a user response (the connection remains open)."""

    SUSPEND = "suspend"
    """End this turn and wait for another request to resume it."""


#: Sentinel for when ``result`` is not overridden — ``None`` is a valid result and cannot mean "not supplied".
UNSET = object()


@dataclass
class Interaction:
    """How execution proceeds after a tool runs."""

    kind: InteractionKind = InteractionKind.CONTINUE
    result: Any = UNSET
    """``CONTINUE``: replace the tool result with it (or retain the original if absent)."""

    marker: Dict[str, Any] = field(default_factory=_empty_dict)
    """``SUSPEND``: record the awaited response and re-enter from it on resumption."""

    waiter: Optional[AsyncIterator[Any]] = None
    """``WAIT``: async iterator yielding ``(event or None, result or UNSET)``.

    Events emit heartbeat frames to keep the connection alive during long waits; a result
    other than ``UNSET`` means the user has answered and becomes the tool's final result.
    Natural exhaustion of the iterator means the wait timed out.
    """

    @classmethod
    def proceed(cls, result: Any = UNSET) -> "Interaction":
        return cls(InteractionKind.CONTINUE, result=result)

    @classmethod
    def wait_for(cls, waiter: AsyncIterator[Any]) -> "Interaction":
        return cls(InteractionKind.WAIT, waiter=waiter)

    @classmethod
    def suspend(cls, marker: Optional[Dict[str, Any]] = None, **extra: Any) -> "Interaction":
        m = dict(marker or {})
        m.update(extra)
        return cls(InteractionKind.SUSPEND, marker=m)


@dataclass
class InteractionRequest:
    """A tool handler return value meaning, "I cannot decide this step; ask a person."

    On seeing it, the framework **suspends** the turn (``handle_tool_call`` → SUSPEND): it
    emits :class:`InteractionRequested` and :class:`Suspended`, records the pending
    interaction (kind / tool_call_id / payload) on the session, and concludes the turn as
    SUSPENDED. A user response begins the next turn with ``Query(by=Contributor.ANSWER)``;
    the framework turns it into this call's tool result (``Runner.apply_answer``), and the
    model continues. To the model, this is an ordinary tool returning the user's answer.
    """

    kind: str
    payload: Dict[str, Any] = field(default_factory=_empty_dict)


class StepPlan:
    """How this step starts.

    The default is to call the LLM, but two situations should not do so:

    * **Recovery**: the prior turn already emitted tool calls. After a process restart or
      suspended resumption, **continue dispatching** them; another LLM call would make the
      model repeat the same tool requests.
    * **Bridging**: the product directly injects a tool call for the agent (for example,
      forwarding a child agent's question unchanged to the user), so the model need not speak.

In both cases, this step skips the LLM and directly dispatches the existing calls.
    """

    __slots__ = ("skip_llm", "tool_calls", "stop", "reason")

    def __init__(
        self,
        *,
        skip_llm: bool = False,
        tool_calls: Optional[List[Any]] = None,
        stop: bool = False,
        reason: str = "",
    ):
        self.skip_llm = skip_llm
        self.tool_calls = tool_calls or []
        self.stop = stop
        self.reason = reason

    @classmethod
    def call_llm(cls) -> "StepPlan":
        """Normal case: call the LLM."""
        return cls()

    @classmethod
    def dispatch(cls, tool_calls: List[Any]) -> "StepPlan":
        """Skip the LLM for this step and dispatch these existing tool calls directly.

        Use this for calls injected from nowhere, not ``prepare_dispatch``: the latter
        means "adjust the batch requested by the model" and is not called without such a request.
        """
        return cls(skip_llm=True, tool_calls=list(tool_calls))

    @classmethod
    def finish(cls, reason: str = "stop") -> "StepPlan":
        """Do nothing in this step and end the turn immediately."""
        return cls(stop=True, reason=reason)

    @override
    def __repr__(self) -> str:
        if self.stop:
            return f"<StepPlan finish {self.reason!r}>"
        if self.skip_llm:
            return f"<StepPlan dispatch {len(self.tool_calls)} calls>"
        return "<StepPlan call_llm>"


# ── Tool gates (before_tool) and turn conclusion (on_turn_end) — terminology merged from hooks.py ──


class ToolAction(Enum):
    PROCEED = "proceed"    # Execute unchanged.
    REWRITE = "rewrite"    # Execute ``gate.call`` instead.
    DENY = "deny"          # Skip execution and use ``gate.result``.
    SUSPEND = "suspend"    # Suspend this turn; ``gate.marker`` is the resume payload.


@dataclass
class ToolGate:
    """``before_tool`` decision: proceed / rewrite / deny / suspend."""

    action: ToolAction = ToolAction.PROCEED
    call: Any = None                              # REWRITE: replacement call
    result: Any = None                            # DENY: short-circuit result
    marker: Dict[str, Any] = field(default_factory=_empty_dict)  # SUSPEND: resume payload

    @classmethod
    def proceed(cls) -> "ToolGate":
        return cls(ToolAction.PROCEED)

    @classmethod
    def rewrite(cls, call: Any) -> "ToolGate":
        return cls(ToolAction.REWRITE, call=call)

    @classmethod
    def deny(cls, result: Any) -> "ToolGate":
        return cls(ToolAction.DENY, result=result)

    @classmethod
    def suspend(cls, marker: Dict[str, Any]) -> "ToolGate":
        return cls(ToolAction.SUSPEND, marker=marker)

    @property
    def proceeding(self) -> bool:
        return self.action in (ToolAction.PROCEED, ToolAction.REWRITE)

    @property
    def effective_call(self) -> Any:
        return self.call if self.action is ToolAction.REWRITE else None


class TurnAction(Enum):
    AUTO = "auto"            # Framework default: continue only if this step dispatched tools.
    CONTINUE = "continue"    # Continue even without tools (for new input at a turn boundary, etc.).
    STOP = "stop"            # Conclude here.


@dataclass
class TurnDecision:
    """``on_turn_end`` decision — continue steps in the same run or conclude it."""

    action: TurnAction = TurnAction.AUTO
    reason: str = ""

    @classmethod
    def auto(cls) -> "TurnDecision":
        return cls(TurnAction.AUTO)

    @classmethod
    def keep_going(cls) -> "TurnDecision":
        return cls(TurnAction.CONTINUE)

    @classmethod
    def stop(cls, reason: str = "stop") -> "TurnDecision":
        return cls(TurnAction.STOP, reason=reason)

    def should_continue(self, had_tools: bool) -> bool:
        if self.action is TurnAction.CONTINUE:
            return True
        if self.action is TurnAction.STOP:
            return False
        return had_tools


__all__ = [
    "Interaction",
    "InteractionKind",
    "StepPlan",
    "ToolAction",
    "ToolGate",
    "TurnAction",
    "TurnDecision",
    "UNSET",
]
