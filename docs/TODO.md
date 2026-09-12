# flops_agent TODO

This is the sole source of framework work items. Tutorials and the sample product
link here rather than duplicating this list.

## Completed framework capabilities

- Standard SSE serialization and a stable wire contract through Runtime(wire=...),
  WireCodec, WIRE_TYPES, runtime.sse_stream(run), and cursor injection.
- Session-level cancellation through Runtime.stop_session(), including optional
  shared RunStore stop intent.
- Active-run lookup and persistent session active-run markers.
- Agent persona assembly through Agent.persona(), Agent.recall(), and
  Runner.build_system_prompt().
- Tool packages, capability filtering, tool routing, and the framework navigation
  tools.
- Executor dispatch protocol, dispatch records, and recovery-time re-dispatch.
- InteractionRequest and ANSWER-based suspension and continuation.
- Persistent session suspension state.
- Fine-grained Database persistence and HistoryChanged notification.
- Typed lifecycle seams for tool gates, streaming tool results, and runner hooks.
- A recovery recipe in docs/06-recovery.md.

## Open framework work

1. Define typed protocols for ToolCall, StreamChunk, ToolResult, and RunMeta; reduce
   broad Any usage and extend the AgentEvent union for interaction events.
2. Split RunStore into required logging and optional stop, recovery, and dispatch
   capabilities. Make create_run(id) idempotent without resetting resume evidence.
3. Make keys the only key path. Move deployment-specific crypto context, key stash,
   and SSE crypto out of the framework.
4. Make persistence non-blocking by using asynchronous protocols or consistent
   thread offloading and batching.
5. Correct shutdown completion behavior, make with_overrides preserve all relevant
   configuration, and prevent concurrent runs for one session.
6. Close an interrupted LLM stream and prevent replay duplication after retry.
7. Externalize user- and model-facing copy behind an injectable object with English
   defaults.
8. Remove product-shaped assumptions from Session and fully type ToolContext.
9. Avoid O(n) user-turn recounting in Session.append and align tool success
   conventions between dispatch and runner.
10. Add end-to-end tests for recovery, shutdown, stream retry, concurrent starts,
    with_overrides, denied tools, and ToolRouter. Move product tests out of
    framework-named test modules.

## Product-layer boundaries

The following remain product decisions: mapping HTTP requests to intents,
authentication and authorization, key acquisition, session reconstruction,
deployment routing, device directories, and business-specific tool policy.

The decision rule remains simple: a capability belongs in the framework only if a
third party building a non-Flops agent would also need it.
