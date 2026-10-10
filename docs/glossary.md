# Use consistent runtime and storage terminology

**Reader question:** Do the same words in conversation, code, and documentation mean the same thing?

**Prerequisites:** None. **Outcome:** Distinguish tasks, connections, and persisted objects, and use consistent terms to search interfaces and logs.

## One task

| Term | Meaning | Do not confuse it with |
|---|---|---|
| Runtime | Assembles model, tools, and storage and manages runs | An HTTP service itself |
| Session | A conversation's message history and metadata | A single Run |
| Query | New input, an interaction answer, or a system event | A required object for every continuation |
| Run | One execution's identity, state, and output log | A browser connection |
| Runner | Steps and policy that advance an execution | Agent identity configuration |
| Agent | Identity, instructions, model preference, and memory configuration | A run pool or infrastructure |

Tutorial: [Understand how a task runs](02-core-concepts.md). Code: `src/flops_agent/engine/runtime.py:105`, `src/flops_agent/entities/query.py:37`, `src/flops_agent/entities/agent.py:39`.

## Output and control

| Term | Meaning |
|---|---|
| event | A change in text, tool activity, or execution lifecycle |
| Delivery | One delivery containing event, cursor, and replayed |
| cursor | A server log position, not an event count or authorization credential |
| SSE | An HTTP streaming text format for encoded events |
| Coalescer | The boundary that converts live events into replayable log segments |
| stopped | A run ended in response to a stop request |
| suspended | A run paused for later user interaction to advance the conversation |
| recovery | Reconstructing interrupted execution from persistent evidence, not ordinary browser reconnection |

Tutorials: [streaming subscriptions](03-streaming-and-sse.md), [stopping and answering](05-cancellation-and-suspension.md), [recovery](06-recovery.md). Code: `src/flops_agent/engine/execution.py:62`, `:78`, `:136`.

## Data and extensions

| Term | Meaning |
|---|---|
| Database | A granular storage protocol for Session metadata and messages |
| RunStore | A storage protocol for run metadata, logs, and optional recovery evidence |
| Inbox | Additional input waiting for a turn or step boundary |
| Memory | Product-maintained long-term knowledge recalled for the model |
| projection | A temporary message view for one model request, leaving factual history unchanged |
| compaction | Context control using summaries and related techniques; requires product integration and storage |
| ToolExecutor | The boundary for executing business tools, including remote tasks |
| dispatch record | Correlation evidence for dispatched work, not automatic business idempotency |

Tutorials: [conversation storage](04-sessions-and-persistence.md), [model input](context-window.md), [extension points](customize-runner.md). Code: `src/flops_agent/seams/database.py:23`, `src/flops_agent/seams/run_store.py:25`, `src/flops_agent/tools/registry.py:49`.

## Next steps

Once you know the term, consult the [API contract](api_surface.md). Return to [Learn](README.md) if you need a starting point.
