# 5. Cancellation, Suspension, and Continuing a Turn

A service agent has more control flow than “completed” or “failed.” A user can stop it, a tool can require confirmation, and new input can arrive while work is running. The framework models each case instead of asking the HTTP handler to build a second run loop.

## Cancellation stops a Run; it does not close SSE

Closing a browser only ends a subscription. By default it does not cancel work. Cancel through a run or runtime:

    # When you already hold the run handle.
    await run.stop()

    # Cancel by session. The local pool is preferred; with a RunStore this also
    # records shared stop intent.
    stopped_run_id = await runtime.stop_session("conv-42", owner_id="u-7")

The runner checks for a stop request at chunk and tool boundaries, emits Cancelled, then reaches RunStatus.STOPPED. A remote executor must propagate the stop into its own job system. Product dispatch code can register a cancellation callback with runtime.runs.get(ctx.run_id).on_stop(...) after confirming the run is present. Do not use HTTP disconnect as the only cancellation signal.

## Suspend when a person must decide

A tool can return InteractionRequest when it needs a user's answer. The framework persists the pending interaction, emits InteractionRequested followed by Suspended, and leaves the run ready to continue. The client sends an answer as a new query:

    run = runtime.start(session, Query.answer({"approved": True}))

The answer becomes a tool result in history before the runner resumes. That is important: the model sees the same durable evidence after a refresh or restart.

Runner.after_execute() has a related but different control seam. It returns an Interaction that can continue, wait in place, or suspend after a tool result. Use InteractionRequest when a tool asks a person for a durable answer; use Interaction when runner policy controls the next step. Keep the distinction in product code rather than building a parallel pending-confirmation state machine.

## Accept input while a run is active

Runtime.deliver(session_id, message, deliver="turn" | "step") places new input in the configured Inbox instead of interrupting current work.

- deliver="turn" waits for the current tool loop to finish and then continues the same run as a new user turn.
- deliver="step" inserts at the nearest loop boundary: after the current tools finish and before the next model request.

A turn boundary is also a step boundary. If the model finishes before another tool step, a waiting step message is delivered at the turn boundary rather than being stranded after the run ends.

MemoryInbox is the zero-configuration implementation. Multi-worker or cross-process deployments should inject a shared Inbox; messages submitted with no active run remain queued until the next run reaches its first boundary.

Next: [Restart recovery](06-recovery.md).
