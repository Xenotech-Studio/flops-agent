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
- One local Run per session: concurrent `Runtime.start()` calls are rejected
  with `SessionRunActiveError`, while independent sessions remain isolated.
- RunStore creation is idempotent by run id and preserves all replay and
  recovery evidence on a repeated creation call.
- Runtime.with_overrides() preserves all constructor and runtime extension
  configuration not explicitly replaced.
- RunStore and framework Database calls are thread-offloaded on async paths,
  with ordered per-Run and per-session persistence lanes.
- Typed lifecycle seams for tool gates, streaming tool results, and runner hooks.
- A recovery recipe in docs/06-recovery.md.
- Deployment-specific crypto context, key stash, and SSE crypto removed from the
  framework; the kernel's crypto package now holds only aes, transport, and the
  generic field_crypto envelope helper. See docs/api_surface.md's Crypto section.

## Open framework work

1. Define typed protocols for ToolCall, StreamChunk, ToolResult, and RunMeta; reduce
   broad Any usage and extend the AgentEvent union for interaction events.
2. Split RunStore into required logging and optional stop, recovery, and dispatch
   capabilities.
4. Correct shutdown completion behavior.
5. Close an interrupted LLM stream and prevent replay duplication after retry.
6. Externalize user- and model-facing copy behind an injectable object with English
   defaults.
7. Remove product-shaped assumptions from Session and fully type ToolContext.
8. Avoid O(n) user-turn recounting in Session.append and align tool success
   conventions between dispatch and runner.
9. Add end-to-end tests for recovery, shutdown, stream retry, concurrent starts,
    with_overrides, denied tools, and ToolRouter. Move product tests out of
    framework-named test modules.

## Product-layer boundaries

The following remain product decisions: mapping HTTP requests to intents,
authentication and authorization, key acquisition, session reconstruction,
deployment routing, device directories, and business-specific tool policy.

The decision rule remains simple: a capability belongs in the framework only if a
third party building an independent agent would also need it.
