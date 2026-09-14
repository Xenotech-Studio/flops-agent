"""FLOPS agent framework: an installable runtime for service-oriented agents.

This module is the public contract. Names exported here are the supported API;
all other implementation details may be refactored freely.

Quick start
-----------

    async for event in runtime.run(messages=[{"role": "user", "content": "hi"}]):
        print(event)                                            # each event

Key public types include ``Session`` for conversation history, ``Run`` and
``RunPool`` for execution and subscriptions, ``Delivery`` for replay cursors,
and ``Runtime``/``Runner`` for orchestration. The event stream exposes text,
reasoning, tool-call, tool-result, completion, suspension, cancellation, and
error events. ``WIRE_TYPES`` and the SSE helpers provide the standard transport
vocabulary. Seams such as ``Database``, ``RunStore``, ``Inbox``,
``LLMStreamClient``, and ``ToolExecutor`` are product-provided boundaries;
their in-memory implementations support zero-configuration use.

The ``engine``, ``entities``, ``seams``, ``providers``, ``crypto``, and
``tools`` subpackages are re-exported here as convenience namespaces.
"""

from .providers.openai import OpenAIStreamClient
from .entities.contracts import (
    FinishStreamChunk,
    DispatchRecord,
    OpenAIToolCall,
    ReasoningStreamChunk,
    StreamChunk,
    TextStreamChunk,
    ToolCall,
    ToolArguments,
    ToolCallStreamChunk,
    ToolFunction,
    ToolOutcome,
)
from .seams.conversation_store import ConversationStore
from .entities.events import (
    HistoryChanged,
    AgentEvent,
    Cancelled,
    Error,
    LoopFinished,
    ProductEvent,
    ReasoningDelta,
    StepCompleted,
    Suspended,
    TextDelta,
    ToolCallArgsDelta,
    ToolCallReady,
    ToolCallStarted,
    ToolExecuting,
    ToolResult,
    ToolResultDelta,
)
from .seams.executor import ToolExecutor
# ToolGate and TurnDecision live in interaction; StepPlan and Interaction use
# a single entity representation.
from .engine.interaction import (
    ToolAction,
    ToolGate,
    TurnAction,
    TurnDecision,
)
from .seams.llm_client import LLMStreamClient
from .entities.agent import Agent, Memory
from .seams.database import Database, InMemoryDatabase, sync_session
from .seams.inbox import Inbox, MemoryInbox
from .seams.run_store import RunMeta, RunStore, InMemoryRunStore
from .safety import Inspector, Review, Rule, Verdict, fallback_decision, scan
from .engine.interaction import Interaction, InteractionKind, StepPlan, UNSET
from .engine.execution import (
    Coalescer,
    Delivery,
    LogWrite,
    PassthroughCoalescer,
    Run,
    RunPool,
    RunStatus,
)
from .entities.query import Contributor, Query
from .engine.runtime import Runtime, LLMStreamRetryPolicy
from .engine.runner import Runner
from .engine.stream import StreamAccumulator
from .entities.session import MessageNotFound, Session, TruncationNeedsConsent
from .tools.registry import DEFAULT_REGISTRY, ROUTE_EXECUTOR, ToolContext, ToolRegistry
from .tools.on_executor import register_on_executor_package
from .tools.navigation import register_navigation_tools
from .tools.ask_user import register_ask_user_question
from .executor import DispatchLedger, ExecutorLink, InMemoryDispatchLedger
from .engine.interaction import InteractionRequest
from .entities.events import InteractionRequested, InteractionResolved
from .wire import WIRE_TYPES, WireCodec, DEFAULT_CODEC, event_to_wire, to_sse, delivery_to_sse

# Subpackages in __all__ must be real attributes; otherwise ``from flops_agent
# import *`` and hasattr checks fail (lazy imports do not work here).
from . import crypto, executor, tools  # noqa: E402,F401

__all__ = [
    "OpenAIStreamClient",
    "Agent",
    "Memory",
    # Facade
    "Runtime",
    "Runner",
    "Session",
    "Run",
    "RunPool",
    "RunStatus",
    "Delivery",
    "Coalescer",
    "PassthroughCoalescer",
    "LogWrite",
    "LLMStreamRetryPolicy",
    "StreamAccumulator",
    "Database",
    "RunStore",
    "InMemoryRunStore",
    "RunMeta",
    "InMemoryDatabase",
    "sync_session",
    "Inbox",
    "MemoryInbox",
    "HistoryChanged",
    "Verdict",
    "Rule",
    "Review",
    "Inspector",
    "scan",
    "fallback_decision",
    "Interaction",
    "InteractionKind",
    "StepPlan",
    "UNSET",
    "Query",
    "Contributor",
    "MessageNotFound",
    "TruncationNeedsConsent",
    # Typed extension contracts
    "ToolFunction",
    "ToolCall",
    "OpenAIToolCall",
    "ToolArguments",
    "DispatchRecord",
    "TextStreamChunk",
    "ReasoningStreamChunk",
    "ToolCallStreamChunk",
    "FinishStreamChunk",
    "StreamChunk",
    "ToolOutcome",
    # Events
    "AgentEvent",
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
    "Cancelled",
    "Error",
    "ProductEvent",
    # Wire (default SSE serialization; products override only deltas)
    "WIRE_TYPES",
    "WireCodec",
    "DEFAULT_CODEC",
    "event_to_wire",
    "to_sse",
    "delivery_to_sse",
    # Extension points
    "LLMStreamClient",
    "ConversationStore",
    "ToolExecutor",
    "ToolContext",
    "ToolRegistry",
    "DEFAULT_REGISTRY",
    "register_navigation_tools",
    "register_ask_user_question",
    "register_on_executor_package",
    "ROUTE_EXECUTOR",
    "ExecutorLink",
    "DispatchLedger",
    "InMemoryDispatchLedger",
    "InteractionResolved",
    "InteractionRequested",
    "InteractionRequest",
    # Control signals
    "ToolGate",
    "ToolAction",
    "TurnDecision",
    "TurnAction",
    # Subpackages
    "crypto",
    "executor",
    "tools",
]

__version__ = "0.2.0"
