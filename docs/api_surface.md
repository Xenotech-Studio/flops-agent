# Public API surface

This document describes the supported surface of `flops_agent` for embedding
applications. The package root is the primary facade; its `__all__` list is the
authoritative list of stable top-level imports. Submodules with their own
`__all__` provide a supported namespace where noted below.

## Runtime and lifecycle

`Runtime`, `Runner`, `Run`, `RunPool`, `RunStatus`, `Delivery`, and `LogWrite`
form the lifecycle API. `Runtime` assembles application-provided seams and
starts runs; subscribers observe deliveries without driving execution.

`Session`, `Query`, `Contributor`, `Agent`, and `Memory` describe conversation
state and agent identity. Hosts may adapt field names at their boundary, but the
framework does not prescribe an application message schema.

## Events and wire format

The public event types, `AgentEvent`, `WIRE_TYPES`, `WireCodec`, `event_to_wire`,
`to_sse`, and `delivery_to_sse` form the standard streaming contract. Products
own HTTP routing, authorization, client identity, and replay policy around that
contract.

## Typed extension seams

`LLMStreamClient`, `ToolExecutor`, `Database`, `RunStore`, `Inbox`, and their
in-memory implementations are extension seams. The typed provider boundary uses
`StreamChunk`, `TextStreamChunk`, `ReasoningStreamChunk`, `ToolCallStreamChunk`,
and `FinishStreamChunk`. Tool execution uses `ToolCall`, `ToolArguments`, and
`ToolOutcome`; persistence and remote dispatch use `RunMeta` and
`DispatchRecord`.

`ToolGate`, `TurnDecision`, `Interaction`, `InteractionRequest`, and
`StepPlan` support policy extensions without requiring an application-specific
runner implementation.

## Tools

The `flops_agent.tools` namespace provides the registry and dispatch protocol.
`ToolRegistry`, `DEFAULT_REGISTRY`, `ToolContext`, `ToolRouter`, and
`register_on_executor_package` are public integration points. Tool schemas,
capabilities, routing, and safety rules remain application policy.

## Crypto

`flops_agent.crypto` provides generic primitives only. Construct a
`TransportKey` from PEM bytes already resolved by the embedding application and
pass that value explicitly to transport operations. The kernel does not load
keys from files or environment variables and does not maintain ambient key
state.

`crypto.field_crypto` operates on caller-selected field names. Applications own
field conventions, key acquisition, key lifetime, and authorization protocols.

## Compatibility

Public names follow semantic versioning. Names outside the documented facade or
a submodule's explicit `__all__` are implementation details and may change in a
minor release. See [CHANGELOG.md](CHANGELOG.md) for release notes.
