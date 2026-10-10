# API contract

**Reader question:** Which entry point should I call, and what are its parameters, return values, and responsibilities?

**Prerequisites:** Understanding [Session, Query, and Run](02-core-concepts.md) is recommended, but this page also supports direct lookup. **Outcome:** Choose the right public entry point and follow the code references to verify exact signatures.

This is a task-oriented index of the basic v0.2.0 integration contract, not a stability promise for every internal symbol. Prefer public imports from flops_agent; the installed version's __all__ is authoritative for its complete export surface.

## Runtime and session operations

| Entry point | Return value and semantics | Code location |
|---|---|---|
| Runtime(llm=…, database=…, run_store=…, tools=…) | Assembles boundaries; runner, executor, inbox, wire, and agent are injectable | engine/runtime.py:147 |
| await load_session(id, owner_id=…, keys=…) | Returns Session; the product authorizes access and keys pass through to storage | engine/runtime.py:961 |
| start(session, query=None, run_id=None, keys=None, context=None) | Returns Run immediately and executes in the background; arguments after session/query are keyword-only | engine/runtime.py:1041 |
| ask(text, session=None) | Asynchronously yields events directly, not Delivery | engine/runtime.py:1218 |
| await save_session(session, keys=…) | Persists edits made outside the framework | engine/runtime.py:1009 |
| await stop_session(id, owner_id=…) | Returns target run_id or None; requests a stop without guaranteeing that a tool has already exited | engine/runtime.py:317 |
| deliver(id, message, when="turn") | Enqueues synchronously; when is turn or step; does not create a Run | engine/runtime.py:876 |
| await shutdown() | Interrupts active runs and returns the number handled | engine/runtime.py:1258 |
| await recover(resume, on_gave_up=…) | Returns the number of recovery tasks scheduled asynchronously, not the number successful | engine/runtime.py:1154 |

Paths in the table are relative to `src/flops_agent/`. The product interprets context. Omitting Query means continuing from the current Session state; there is no generic regenerate=True parameter. To regenerate, first edit and persist history according to user intent.

Query.text constructs ordinary input; Query.answer constructs an interaction answer; Query.event constructs an external system contribution. Session truncation or rewind may require explicit consent; see [conversation storage](04-sessions-and-persistence.md).

## Subscriptions, events, and wire format

| Entry point or value | Contract |
|---|---|
| Run.id / status / error | Execution identity, state, and failure exception; states are running, done, stopped, failed, suspended |
| run.subscribe(from_cursor=0) | Asynchronously yields Delivery, replaying the log before live output |
| Delivery.event / cursor / replayed | Content, server log position, and replay flag; do not infer cursor from client frame counts |
| await run.wait() | Waits for termination and returns RunStatus |
| await run.stop() / run.on_stop(callback) | Requests a stop / registers an executor cancellation callback |
| runtime.sse_stream(run, from_cursor=0) | Asynchronously yields SSE strings; the product implements HTTP and authorization |
| WireCodec.event_to_wire / to_sse / delivery_to_sse | Extension points for event conversion, SSE encoding, and Delivery encoding |

Common events include TextDelta, ReasoningDelta, ToolCallStarted, ToolResult, LoopFinished, Cancelled, Suspended, and Error. Use ProductEvent for custom product notifications. See WIRE_TYPES for standard wire types.

The default WireCodec adds cursor only to live JSON frames; replay and pre-serialized strings pass through unchanged. The default PassthroughCoalescer does not merge events. A custom Coalescer's feed/flush can change log granularity, so a replayed event may be serialized log data rather than the original event instance.

Code: `src/flops_agent/engine/execution.py:62`, `:78`, `:603`, `:658`; `src/flops_agent/wire.py:71`, `:170`. For the procedure, see [streaming subscriptions](03-streaming-and-sse.md).

## Storage and extension protocols

### Database: conversation facts

The synchronous protocol includes load_meta, load_messages, count_messages, append_messages, truncate_messages, replace_message, patch_meta, and create_session. It addresses data by session_id and owner_id; keys is a storage-defined parameter. Asynchronous framework paths offload I/O to worker threads, which backends must support.

Code: `src/flops_agent/seams/database.py:23`. InMemoryDatabase stores only process-local data. Append, truncation, and tail rewrites do not replace precise edits to arbitrary messages in the middle of history.

### RunStore: execution evidence

Basic log operations include create_run, append_chunks, buffer_range, mark_finished, and metadata reads. For an existing id, create_run is create-if-absent: it must not clear old logs, stop intent, or recovery evidence, or reset terminal state to running.

Additional capabilities include latest-run lookup and stop intent, active-record enumeration and mark_resuming, and dispatch records. mark_resuming returns 0 when the recovery budget is exhausted. Persistent backends must preserve log ordering and state consistency; RunStore is not a built-in distributed scheduler.

Code: `src/flops_agent/seams/run_store.py:25`; recovery orchestration: `src/flops_agent/engine/runtime.py:1154`.

### Model, executor, inbox, and memory

LLMStreamClient's `async acompletion(**request)` returns an async chunk stream. ToolExecutor's `async execute(call, ctx)` runs business tools. Inbox supplies queued input at turn/step boundaries; Runtime.deliver works only with implementations that have push. Memory's recall returns request text and remember maintains product knowledge storage.

Code: `src/flops_agent/seams/llm_client.py:18`, `src/flops_agent/seams/executor.py:1`, `src/flops_agent/seams/inbox.py:1`, `src/flops_agent/entities/agent.py:26`.

## Tools and policy

tools accepts functions, OpenAI-style schemas, or `{"schema": schema, "fn": handler}`. Minimal function-signature inference declares parameters as strings; supply explicit schemas for precise types. ToolRegistry manages packages, visibility, and routing. ToolContext carries runtime, session, run_id, tool_call_id, and remote-dispatch correlation information.

Runner.before_tool returns ToolGate: deny skips execution and returns a result to the model; proceed continues. plan_step chooses a model request, direct dispatch, or finish; after_execute controls post-tool interaction. Agent holds identity, instructions, model preference, and optional Memory, not execution infrastructure.

Code: `src/flops_agent/tools/schema.py:17`, `:42`; `src/flops_agent/tools/registry.py:49`, `:164`; `src/flops_agent/engine/runner.py:638`, `:1383`. Tutorials: [tools](07-extending.md) and [policy extensions](customize-runner.md).

## Cryptography and authorization boundaries

flops_agent.crypto supplies generic cryptographic primitives. Construct TransportKey from PEM bytes already obtained by the product and pass it explicitly; field_crypto operates on product-selected fields. The framework does not discover credential files or replace identity authentication, authorization, key acquisition, or retention policy.

Code: `src/flops_agent/crypto/transport.py:23`, `:57`; `src/flops_agent/crypto/field_crypto.py:52`.

## Versions and next steps

Use [released changes](CHANGELOG.md) to confirm upgrade scope. Do not infer installed-package capabilities from new methods on the current branch. Investigate behavior in [errors and limits](errors-and-limits.md), or return to [Learn](README.md) for step-by-step guidance.
