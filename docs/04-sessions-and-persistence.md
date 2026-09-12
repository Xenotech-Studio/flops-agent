# 4. Sessions and Persistence: Treat History as Evolving Data

A short script can retain messages in memory. A service cannot: history must load on the next request, reconnect logs must be visible across workers, and regeneration must not overwrite thousands of records. The framework therefore persists conversations and runs separately.

## Database stores a Session

Database stores session metadata and messages. It is not a broad load()/save() object. It offers fine-grained primitives for metadata reads, ranged message reads, counts, append, truncate, indexed replacement, metadata patches, and creation.

    runtime = Runtime(llm=my_llm, database=my_database)

    session = await runtime.load_session("conv-42", owner_id="u-7", keys=request_keys)
    run = runtime.start(session, Query.text("Continue."), keys=request_keys)
    async for _ in run.subscribe():
        pass

The default runner persists history at appropriate boundaries; normally you do not save it yourself. If product code edits a Session outside the framework, call await runtime.save_session(session, keys=...). Use load_session_sync() and save_session_sync() only in synchronous scripts.

Why so many operations? Appending new messages must not rewrite old history; regeneration truncates; continuation replaces a tail message; changing a title should not read or encrypt an entire conversation. sync_session() selects a safe append, truncate, or last-message rewrite from a length change. For a special edit in the middle, call Database.replace_message() at that index rather than hoping one whole-session save will identify it.

keys is a pass-through slot. A zero-knowledge product can provide its own key shape per request; the framework neither interprets, caches, nor persists it.

InMemoryDatabase is a semantically complete reference implementation for examples and tests. It disappears with the process and is not production storage.

To implement Database, provide synchronous versions of load_meta, load_messages, count_messages, append_messages, truncate_messages, replace_message, patch_meta, and create_session. They address a session by session_id and optional owner_id; encrypted reads and writes also receive untouched keys. load_session(), save_session(), and normal runner persistence move synchronous work off the event loop. A low-level runtime-state patch_meta() may still run in the current thread, so a network-backed implementation must supply a fast non-blocking adapter rather than doing slow RPC inside these synchronous methods.

## RunStore stores a Run

Database answers “what did this conversation say?” RunStore answers “where did this turn get to, and which output should a browser replay?” With Runtime(run_store=my_store), the framework records run metadata on creation, persists each completed log segment, and stores terminal status.

    runtime = Runtime(llm=my_llm, database=my_database, run_store=my_run_store)

Cross-process reconnection, stop intent, and restart recovery rely on optional RunStore capabilities. InMemoryRunStore is suitable for single-process demos and protocol tests. A production store is normally shared and keeps append_chunks(), buffer_range(), and status updates consistent for the same run id.

Capability support is deliberately incremental. Logs and terminal status are enough for a current process. Latest-run lookup and stop intent enable cross-worker stop requests. Active-run enumeration, resume counts, and dispatch records add restart recovery and duplicate-dispatch avoidance. Review the next two articles against the capabilities your deployment implements.

## Edit a Session safely

Session offers truncate_after(), truncate_before(), and rewind_to_user_turn() by message id. If an edit could discard another user's turn, these require consent=True or raise TruncationNeedsConsent. This guard prevents silently deleting history before the product has confirmed intent in its UI.

A normal regeneration flow is: identify and truncate at the product boundary, persist that edit, then call runtime.start(session) with no query. Do not invent a regenerate=True parameter that duplicates session state.

## Projection and compaction are different

Persisted Session.messages should retain facts. Messages sent to a model may be a temporary view. The default Runner.project_messages() returns history unchanged; a product can override it to trim old tool output, images, or long text before each request. This read-time projection does not modify history or need separate storage.

Context compaction calls a model to produce a summary, then persists it as a CompactionRecord in CompactionStore. The framework provides ProjectionConfig, CompactionPolicy, planning, and quality checks in flops_agent.engine.compaction; Runtime does not automatically wire them. Window discovery, summary-model routing, write permissions, and summary storage remain product policy. Article 7 shows where that integration belongs.

Next: [Cancellation and suspension](05-cancellation-and-suspension.md).
