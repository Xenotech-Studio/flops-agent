# Decide whether the framework fits your task

You have a Python product and want a model to complete a task: accept input, call tools, stream output, and maintain clear state when a user stops work or a connection drops. flops-agent provides the runtime kernel for that process. You still choose the model, product interfaces, and storage backends.

**Reader question:** Which part of my product should this framework handle, and where should I start?

**Prerequisites:** You can run Python; no prior knowledge of agent terminology is required. **Outcome:** Distinguish framework responsibilities from product responsibilities and choose a learning path you can complete.

## Establish the boundaries

| What you need | What the framework provides | What you supply |
|---|---|---|
| Complete a model-and-tool interaction | Runtime, Runner, and tool dispatch | A model client and tool business logic |
| Deliver results incrementally to a browser | Events, subscriptions, SSE encoding, and replay cursors | HTTP routes, authentication, and client rendering |
| Continue a conversation on the next request | Session and the Database protocol | A persistent backend and user isolation policy |
| Stop, wait for an answer, or recover after restart | Run state, interactions, and recovery orchestration | Product entry points, recovery context, and tool idempotency |

This is not a hosted chat product you can deploy and sign into. The repository example is not an HTTP service listening on a port either. Start with the offline example, which needs no model account, then replace the product boundaries. That is easier than debugging networking, authentication, and execution at the same time.

## The basics

1. [Run a task in five minutes](01-quick-start.md): execute a deterministic example and observe tool results and memory output.
2. [Understand how a task runs](02-core-concepts.md): work backward from that output to Runtime, Session, Query, and Run.

If you have not run the example yet, do only the first step now. The remaining chapters can wait until you have a successful run.

## Build a service

- [Connect your own model](connect-model.md): replace the offline model and check the first output from a real endpoint.
- [Keep receiving results after a disconnect](03-streaming-and-sse.md): separate tasks from connections and reconnect with server cursors.
- [Stop work and accept user input](05-cancellation-and-suspension.md): choose different actions for stop buttons, confirmation cards, and additional messages.

## Save and recover

- [Save a conversation and continue it](04-sessions-and-persistence.md): distinguish conversation history from run logs before connecting storage.
- [Recover tasks after a process restart](06-recovery.md): reconstruct tasks from persisted data and verify that side effects are not duplicated.
- [Control model input in long conversations](context-window.md): preserve complete history while constructing a bounded view for the model.

## Extend and integrate

- [Give your agent a tool](07-extending.md): start with an ordinary function and verify that its result reaches the next model request.
- [Put product policy at the right extension point](customize-runner.md): choose Runner, Memory, or Executor rather than copying the loop.
- [Integrate the example into your product](08-worked-example.md): complete the request, subscription, authorization, storage, and stop paths one at a time.

## Next steps

For your first run, start with [the five-minute guide](01-quick-start.md). If you already know what you need to look up, switch to [Reference](reference.md). These docs use released v0.2.0 capabilities as their baseline; the [changelog](CHANGELOG.md) lists released versions only.

## Code evidence

- `src/flops_agent/engine/runtime.py:147`: product-supplied model, storage, executor, and runner.
- `src/flops_agent/engine/runtime.py:1041`: an execution entry point independent of subscriptions.
- `docs/sample_product/server.py:1`: the example explicitly describes itself as integration reference code, not a production HTTP service.
