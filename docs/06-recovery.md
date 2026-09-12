# 6. Restart Recovery: Continue After a Process Dies

A stop button is intentional; a reload, deployment, or crash is not. Losing a run
during a tool call leaves users without an answer and can duplicate side effects
when work is restarted. The framework orchestrates recovery in Runtime while the
product supplies the facts needed to reacquire a session, keys, and its entry
point.

## Mark interruption on shutdown

Call this from the product's SIGTERM or application shutdown hook:

    await runtime.shutdown()

It marks in-flight runs as interrupted, wakes subscribers so clients can reconnect,
and does not pretend that they completed. The process can then release background
tasks. Interrupted state in RunStore is the clue used by the next process. Any
product shutdown work, such as clearing temporary zero-knowledge key material,
belongs in the same hook.

## Let Runtime orchestrate startup recovery

After wiring database, run_store, and product hooks, but before accepting traffic:

    async def resume(meta) -> None:
        # meta comes from your RunStore; its shape is implementation-defined.
        session = await runtime.load_session(meta.session_id, owner_id=meta.owner_id)
        runtime.start(session, run_id=meta.run_id)

    await runtime.recover(
        resume,
        on_gave_up=lambda meta: runtime.clear_session_active_run(
            meta.session_id, owner_id=meta.owner_id, run_id=meta.run_id,
        ),
    )

A real product often does not call start() directly. It re-enters its normal chat
entry point with the original run_id, so authentication, key acquisition, and
session lookup follow the ordinary request path. Reusing the run_id is essential:
a client carrying that id and cursor can reconnect to its existing log.

Runtime.recover() enumerates recoverable runs, clears orphaned records, calls
mark_resuming() so storage can enforce a retry budget, and schedules resume(meta)
asynchronously. If repeated failures exhaust the budget, mark_resuming() returns
0 and the framework calls on_gave_up(meta); that callback should clear product
pointers to the abandoned run.

The return value is the number of recovery tasks scheduled, not the number that
already succeeded. Track those tasks in product observability and decide when
your deployment should accept traffic.

## A tool that was running halfway through

RunStore may implement record_dispatch(), pending_dispatches(), and
clear_dispatches(). The framework records a call before dispatch. When recovery
finds an unfinished record and matching history, the first step re-dispatches it
rather than asking the model a second time. A remote executor should record its
own task id through ToolContext.record_dispatch(), then reuse or wait for that
task when ctx.resume_of is present.

Recovery is not an InMemoryRunStore feature: memory is gone when the process exits.
A production store needs cross-process logs, state, latest-run lookup, and, when
needed, shared stop intent.

Finally, classify side effects. Retrying a read-only lookup and creating an order
or executing a command are not equivalent. Dispatch records let the executor
receive ctx.resume_of, but business idempotence exists only if the executor records
the first remote task and reuses or queries it on recovery.

Next: [Extending the framework](07-extending.md).
