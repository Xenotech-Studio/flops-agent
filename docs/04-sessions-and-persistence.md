# Save a conversation and continue it

**Reader question:** How does the next request pick up history, and why is there a separate run log?

**Prerequisites:** Complete [the two-turn example](02-core-concepts.md). **Outcome:** Distinguish the two stores, continue the same conversation, and recognize when you need a persistent backend.

## Decide what you are storing

| Data | Storage boundary | Purpose |
|---|---|---|
| Conversation metadata and message history | Database | Let the next model turn see the conversation so far |
| One run's state and output log | RunStore | Subscription replay, stop intent, and recovery evidence |
| Long-term knowledge extracted from conversations | Memory | Product-defined recall and maintenance |

Saving a Session does not mean a browser can replay every streaming frame from a Run. Saving a run log does not mean you have the full conversation history needed for the next model request.

## Try it: continue the same conversation

The [basic example](02-core-concepts.md) uses InMemoryDatabase and already demonstrates reloading the same id in one process. Keep that flow when integrating your product:

```python
from flops_agent import Runtime, Query

runtime = Runtime(llm=client, database=database, run_store=run_store)
# Your product assembles client, database, and run_store at startup.
session = await runtime.load_session(session_id, owner_id=authenticated_owner_id)
run = runtime.start(session, Query.text("Continue the previous question."))
async for delivery in run.subscribe():
    consume(delivery)  # Your response or rendering logic
```

The default Runner persists history at execution boundaries; normal requests do not need another whole-Session save. Call `await runtime.save_session(session)` when product code edits a conversation outside the framework. For an edit in the middle of history, use the precise replacement interface rather than expecting a whole-session save to discover arbitrary changes.

## Replace memory with persistent storage

InMemoryDatabase and InMemoryRunStore are reference implementations whose data disappears on process exit. To retain data across restarts, implement and inject your own backends.

Database requires granular operations for metadata reads, ranged message reads, counts, append, truncation, replacement, metadata patches, and session creation. RunStore manages creation, log append, ranged replay, and terminal state. Cross-process stops and recovery require additional capabilities. Consult the [API contract](api_surface.md) for the methods.

Both protocols use synchronous methods. Asynchronous framework paths offload storage calls to worker threads, so backends must support those calls. The framework's per-session lock is not a cross-process database transaction; the backend must still provide appropriate concurrency and consistency. owner_id is a data-addressing parameter, not a substitute for authentication.

## Editing and long conversations

Session supports truncation by message id and rewinding by user turn. When an edit could discard later user input, it requires explicit `consent=True` or may raise TruncationNeedsConsent. Confirm the user's intent first, then edit and persist history, and use `runtime.start(session)` to continue when appropriate.

Editing history changes the factual record. Temporarily shortening model input is a different operation; see [controlling input in long conversations](context-window.md).

## Check the result

Send two turns with the same owner_id and session_id, reload, and inspect the messages. Switch owner_id and verify that your backend isolates data as intended. Once you have real persistent storage, restart the process and check that history remains. An in-memory backend should not pass that last test.

## Next steps

After storage works, test [restart recovery](06-recovery.md). Do not assume every tool can resume automatically.

## Code evidence

- `src/flops_agent/seams/database.py:23`: the granular Database protocol.
- `src/flops_agent/engine/runtime.py:961`, `:1009`: asynchronous loading and saving.
- `src/flops_agent/seams/run_store.py:25`: run-log storage protocol.
- `src/flops_agent/entities/session.py:138`: consent checks for history truncation.
