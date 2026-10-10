# Recover tasks after a process restart

**Reader question:** How can interrupted work resume without repeating tool side effects?

**Prerequisites:** Connect [persistent storage](04-sessions-and-persistence.md) and understand [stopping and suspension](05-cancellation-and-suspension.md). **Outcome:** Implement a recovery callback and design an interruption test with observable results.

## Meet the recovery prerequisites

Recovery does not happen just because you create another Runtime. You need a persisted Session, a RunStore with recovery enumeration and retry budgets, and a product entry point that can rebuild the model, authorization context, and tool environment. InMemoryRunStore loses its evidence when the process exits and cannot provide recovery across a real restart.

During a normal shutdown, call `await runtime.shutdown()` from the product's shutdown hook to mark active runs as interrupted. A hard crash does not run that hook. Your storage implementation must identify abandoned active records, and your product must prevent multiple instances from recovering the same task at once.

## Try it: rebuild the execution entry point

This is an integration sketch, not a standalone script. Runtime is already connected to persistent backends. Add product authorization and context reconstruction inside resume:

```python
async def resume(meta):
    session = await runtime.load_session(meta.session_id, owner_id=meta.owner_id)
    runtime.start(session, run_id=meta.run_id)

async def gave_up(meta):
    await runtime.clear_session_active_run_async(
        meta.session_id, owner_id=meta.owner_id, run_id=meta.run_id,
    )

scheduled = await runtime.recover(resume, on_gave_up=gave_up)
```

Keep the original run_id so the existing log still identifies the same execution. Without new user input, no new Query is needed. Encrypted storage or specialized product entry points also require their context to be reacquired; the sketch does not do that for you.

`recover()` enumerates recovery candidates, calls the store's mark_resuming, and schedules resume asynchronously. It returns **the number scheduled**, not the number successfully recovered. When the budget is exhausted, it calls on_gave_up. The sample_product startup callback contains a placeholder pass; it is not a complete recovery implementation.

## Handle a partially executed tool

A remote tool may finish its operation before the result reaches Session. Rerunning the model could submit an order or perform a write twice.

RunStore dispatch records and ToolContext's record_dispatch and resume_of provide correlation evidence. The executor should retain the remote task identifier and query or reuse the first task during recovery instead of blindly creating another. A dispatch record is not an exactly-once business guarantee. Idempotency keys, result queries, and retry policies belong to the tool implementation.

## Check the result

1. Start a run with a test tool whose task id can be queried. Save run_id and the last cursor.
2. Interrupt the process after the executor accepts the task. Confirm that Session and run evidence remain in storage.
3. Start a new Runtime and call recover. Observe the callback, final state, and errors, not just scheduled.
4. Query the tool system and confirm the business operation happened once. Reconnect to the original run_id and verify your product replay path.
5. Simulate recovery failure and check that retrying ends and product pointers are cleaned up when the budget is exhausted.

## Next steps

Once recovery works, consider [long-conversation input](context-window.md). For exact storage capabilities, see the [API contract](api_surface.md).

## Code evidence

- `src/flops_agent/engine/runtime.py:1154`, `:1258`: recovery scheduling and shutdown semantics.
- `src/flops_agent/engine/runner.py:666`: recovery planning for unfinished dispatches.
- `src/flops_agent/tools/registry.py:49`, `:83`: execution context and dispatch records.
