# What is flops-agent

flops-agent is a Python framework for embedding an agent runtime in your backend service. It provides the core loop of model calls, tool execution, and result feedback, along with sessions, run state, and streaming output. Connect your model client and business tools, start with the default loop, and add product logic through Runner, Memory, and executor interfaces.

## Why use it

- **Start with an established loop.** Reuse model calls, tool dispatch, and result feedback so you can focus on business tools and product interfaces.
- **Extend product behavior as needed.** Use Runner hooks to adjust model input, checks before tool execution, and interactions within the existing loop.
- **Build a service.** Runs are independent of client subscriptions, so you can embed the agent in a backend and stream results to clients.
- **Reach your business resources.** Tools can run on the server or access user devices and shared resources through remote executors you integrate.

## What it does

- **Call business tools.** Supply Python functions or tool definitions with handlers to Runtime; execution results enter the session for the next model call.
- **Connect memory and session storage.** Agent reads and updates memory through Memory, while Session uses Database for persistence; you provide memory retrieval logic and persistent backends.
- **Stream output and reconnect.** Runs retain events and server cursors so clients can reconnect and replay output through your product's subscription endpoint.
- **Stop, wait, and recover.** The framework supports stop requests, waiting for user input, and restart recovery when you supply persistent storage and recovery callbacks.

## Start here

Run the offline example in [Run a task in five minutes](01-quick-start.md) to observe tool calls and memory output without an API key. Then read [Understand how a task runs](02-core-concepts.md) to learn how Runtime, Session, Query, and runs fit together before connecting a business tool of your own. For interface details, go to [Reference](reference.md).

## Boundaries

Version v0.2.0 is a public beta. The offline example demonstrates the loop; a real integration still needs a model client, HTTP endpoints, persistent backends, and authorization logic. Validate tool arguments, idempotency, and recovery paths. Recovery after a process restart requires persisted sessions and run records, plus product code to reconstruct the execution context; the default in-memory stores cannot provide it.

To centralize enterprise data or let a cloud-hosted agent call tools on employee computers and shared devices, integrate the storage interfaces and remote execution protocol. The framework provides cryptographic primitives, but your product determines storage location, key management, device connections, and access permissions. Employee privacy protections require separate product design and validation.

## Continue reading

### Build a service

- [Connect your own model](connect-model.md)
- [Keep receiving results after a disconnect](03-streaming-and-sse.md)
- [Stop work and accept user input](05-cancellation-and-suspension.md)

### Save and recover

- [Save a conversation and continue it](04-sessions-and-persistence.md)
- [Recover tasks after a process restart](06-recovery.md)
- [Control model input in long conversations](context-window.md)

### Extend and integrate

- [Give your agent a tool](07-extending.md)
- [Put product policy at the right extension point](customize-runner.md)
- [Integrate the example into your product](08-worked-example.md)

### Details: responsibilities

| What you need | What the framework provides | What you supply |
|---|---|---|
| Complete a model-and-tool interaction | Runtime, Runner, and tool dispatch | A model client and tool business logic |
| Deliver results incrementally to a browser | Events, subscriptions, SSE encoding, and replay cursors | HTTP routes, authentication, and client rendering |
| Continue a conversation on the next request | Session and the Database protocol | A persistent backend and user isolation policy |
| Connect long-term memory | Memory interfaces and call timing | Retrieval logic and memory storage |
| Call tools on remote devices | Executor routing and a message protocol | Device connections, tool implementations, and access authorization |
| Stop, wait for an answer, or recover after restart | Run state, interactions, and recovery orchestration | Product entry points, recovery context, and tool idempotency |

## Sources

All paths below are relative to the flops-agent source repository.

- `src/flops_agent/__init__.py:1`, `pyproject.toml:17`: the service-style agent kernel and Python version requirement.
- `src/flops_agent/engine/runner.py:268`, `:315`, `:523`: the agent loop, model calls, tool execution, and result feedback.
- `src/flops_agent/engine/runtime.py:108`, `src/flops_agent/tools/registry.py:195`: model, Python tool, and executor integration.
- `src/flops_agent/engine/runner.py:1040`, `:1383`: model-input projection and policy hooks before tool execution.
- `src/flops_agent/entities/agent.py:26`, `:62`, `:72`: the Memory protocol, recall, and post-turn updates; the product implements storage and retrieval.
- `src/flops_agent/seams/database.py:1`: product-supplied session persistence, including storage and encryption choices.
- `src/flops_agent/engine/runtime.py:1041`, `:336`: execution independent of subscriptions, SSE, and replay cursors.
- `src/flops_agent/engine/execution.py:696`, `src/flops_agent/engine/runner.py:789`, `src/flops_agent/engine/runtime.py:1154`: stop requests, suspension, and recovery orchestration.
- `docs/06-recovery.md:9`: persistent storage, recovery context, crash handling, and tool idempotency requirements.
- `src/flops_agent/tools/dispatch.py:35`, `src/flops_agent/tools/registry.py:134`, `src/flops_agent/executor/link.py:1`: server and remote executor routing, product-owned device connections, and the result delivery protocol.
- `src/flops_agent/tools/on_executor.py:1`: executor file and command tool names and parameter contracts; the executor must implement the handlers.
- `src/flops_agent/crypto/__init__.py:1`, `:14`: cryptographic primitives and product-owned key, identity, and authorization boundaries.
- `docs/01-quick-start.md:21`, `docs/sample_product/server.py:1`: the offline example needs no API key and is not a complete HTTP service.
- `pyproject.toml:21`, `src/flops_agent/__init__.py:206`, `docs/CHANGELOG.md:23`: Beta status and the v0.2.0 version baseline.
