# Stop work and accept user input

**Reader question:** What should happen when a user stops work, answers a confirmation card, or adds another message?

**Prerequisites:** Understand [the separation between execution and subscription](03-streaming-and-sse.md). **Outcome:** Choose the correct entry point for each intent instead of cancelling and restarting everything.

## Choose the action first

| User intent | Action | Result |
|---|---|---|
| Stop the current task | Request a stop | Runner ends at a check boundary with status stopped |
| Decide something a tool needs | Suspend, then submit an answer | The old Run is suspended; a later request advances the conversation |
| Add information during a task | Enqueue it in Inbox | Read at a defined loop boundary without preempting the current tool |

## Try it: stop a run

If you hold the handle, use `await run.stop()`. To stop by session from your product, use:

```python
stopped_run_id = await runtime.stop_session(
    session_id, owner_id=authenticated_owner_id,
)
```

Your request handler supplies runtime, session_id, and the authenticated owner id. A returned id means a stop target was found; **it does not mean a remote tool has already stopped**. If you hold a local handle, use `await run.wait()` to observe the final state. Runner handles stop requests at model-chunk and tool boundaries. A remote executor must forward the intent to its own task system.

## Try it: answer a suspended interaction

A tool can return InteractionRequest to ask the user a question. The framework records the pending interaction, emits interaction and suspension events, and ends this run. Your product displays the request. After the user answers, reload the same Session and start a run with the answer:

```python
from flops_agent import Query

session = await runtime.load_session(session_id, owner_id=authenticated_owner_id)
if session.pending_interaction is None:
    raise ValueError("This conversation has no pending interaction")
run = runtime.start(session, Query.answer({"approved": True}))
async for delivery in run.subscribe():
    print(delivery.event)
```

The tool request and product protocol define the answer shape; approved is only an example field. Validate that the answer belongs to the current pending interaction, and reject stale cards or unauthorized submissions. Interaction returned by `Runner.after_execute()` is a separate policy extension point; do not confuse it with a tool's InteractionRequest.

## Try it: queue additional input

```python
runtime.deliver(
    session_id,
    {"role": "user", "content": "Please also consider offline use."},
    when="step",
)
```

`when="step"` reads at the nearest loop boundary. `when="turn"` waits for the current tool loop to finish and continues as a new turn. With no active run, the message stays in Inbox until a later run reaches an input boundary. Calling deliver does not start a task by itself.

The Runtime parameter is **when**. The underlying Inbox push parameter is named deliver. The default MemoryInbox is process-local. If a custom Inbox has no push method, enqueue through its own product API.

## Check the result

Verify each path separately: disconnecting does not stop execution; an explicit stop eventually reaches stopped; a suspended interaction can be answered after reloading the conversation; queued input appears in a later model request. Waiting for an answer and recovering after restart are different mechanisms. Do not use recovery for an ordinary confirmation card.

## Next steps

Interaction state must survive requests. Continue with [saving conversations](04-sessions-and-persistence.md).

## Code evidence

- `src/flops_agent/engine/runtime.py:317`, `:876`: stopping and the when parameter.
- `src/flops_agent/engine/runner.py:789`, `:844`: persisting suspension markers and applying answers.
- `src/flops_agent/engine/execution.py:693`: stop-request semantics.
