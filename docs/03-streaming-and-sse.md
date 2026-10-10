# Keep receiving results after a disconnect

**Reader question:** How can a task keep running after the browser disconnects, and how can the client catch up?

**Prerequisites:** Understand [Session and Run](02-core-concepts.md) and have a product entry point that creates a Run. **Outcome:** Separate execution from connections, save server cursors, and reconnect to the same run_id.

## First connection: start, then subscribe

The background task created by `start()` does not depend on a subscription connection. The HTTP layer writes strings into a response; it does not drive a second model loop.

This integration fragment belongs in an authenticated request handler. Your product has already obtained runtime and session. Route registration depends on your Web framework.

```python
from flops_agent import Query

run = runtime.start(session, Query.text("Start the task"))
# Return run.id to the authorized client for subsequent reconnection.
async for frame in runtime.sse_stream(run):
    yield frame
```

SSE is an HTTP response format. Your product sets the response Content-Type to `text/event-stream`. The framework does not register routes or choose an authentication scheme.

## Reconnect with the server's cursor

The client saves run_id and the cursor it receives. On reconnection, the product first authorizes access to the run, resolves its handle, and passes back the last cursor:

```python
async def reconnect(runtime, run_id: str, last_cursor: int):
    # The caller must already have checked identity and ownership of this run.
    run = runtime.runs.get(run_id)
    if run is None:
        raise LookupError("This process has no handle for that run")
    async for frame in runtime.sse_stream(run, from_cursor=last_cursor):
        yield frame
```

`run.subscribe(from_cursor=0)` first replays the log, then follows live output. Each Delivery contains event, cursor, and replayed. When consuming Python objects directly, live events can be handled by types such as TextDelta or ToolResult. Replayed content may already be serialized log data; do not assume it is the same kind of Python event object.

## Check the result

Start a run that produces output over time. Save a server cursor, close the subscription, and confirm that work continues. Subscribe to the same Run again and verify that replay precedes new output. Do not calculate a cursor by counting frames: logs can be coalesced, so live frame counts need not match log positions.

The default WireCodec injects cursor only into live JSON frames; replay frames and pre-serialized strings pass through unchanged. If a client receives only replay before closing, your product protocol must define how it learns its position. Do not guess the last cursor. See the [API contract](api_surface.md).

## Boundaries

The local RunPool is not a historical run lookup service. Finished runs are removed from the pool, and another process cannot find this process's handles. The function above covers only runs whose local handle remains available. Replay across processes or after completion requires persisted logs and product routing. There is currently no public facade that hydrates a read-only Run by run_id.

Closing a connection is not a stop button. Stopping is explicit; see [stopping and additional input](05-cancellation-and-suspension.md). Use ProductEvent for custom notifications. To change the wire format, extend WireCodec rather than guessing internal state in the client.

## Next steps

Connect the stop button, then decide [how to store conversations and logs](04-sessions-and-persistence.md).

## Code evidence

- `src/flops_agent/engine/runtime.py:336`, `:1041`: SSE bridging and independent execution; the finally block near `:1113` removes finished runs from the pool.
- `src/flops_agent/engine/execution.py:603`: replay followed by live subscription.
- `src/flops_agent/wire.py:170`: the cursor difference between live and replay frames.
