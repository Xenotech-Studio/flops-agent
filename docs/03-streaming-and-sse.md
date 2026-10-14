# Scheduling work with Session and Run

Start a task, step away, come back for its result, then continue with the next piece of work: this is how you use Run as a schedulable unit. Session keeps the conversation history across that work; on this page, you will try it yourself.

## Two concepts for scheduling work

Once you hand work to Runtime, you do not have to stay connected to its output. Run gives you a handle on this execution; Session ties together the inputs and results of successive executions:

| Concept | Its role in this example |
| --- | --- |
| Run | One scheduled execution, with an identifier and status. Execution does not depend on a subscription connection; you can read the result later. |
| Session | The conversation ledger that accumulates user input and replies, so the next Run can continue from that history. |

Conversation history and event replay are separate records: Session holds message history; the Run event log holds replayable output, and `Delivery.cursor` marks your reading position. You will read both below.

## Start a task, then step away

Use Python 3.10 or later and create an environment in a new directory. Installation needs network access; once installed, the script below does not contact a model service or require a key.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install "flops-agent==0.2.0"
```

Create `scheduling.py` and add the two Python blocks from this section and the next, in order. First, define an offline LLM module and a startup function. The LLM module returns text in two parts, giving the work a brief execution period.

```python
import asyncio
from flops_agent import (
    InMemoryDatabase, InMemoryRunStore, Query, Runtime,
    TextDelta, TextStreamChunk,
)


class PseudoLLMStreamClient:
    async def acompletion(self, **kwargs):
        async def stream():
            for text in ("First part. ", "Second part."):
                await asyncio.sleep(0.05)
                yield TextStreamChunk(text=text)
        return stream()


async def start_work(runtime, session):
    run = runtime.start(session, Query.text("Prepare a short report"), run_id="work-1")
    print(f"Started: {run.id}")
    print("Away: doing other work, no subscription")
    await asyncio.sleep(0.01)
    return run

```

`start()` returns a Run immediately, while Runner advances the work in the background. After receiving `run.id`, this function does something else without subscribing or waiting for completion; `sleep()` stands in for other work that yields to the event loop. When you run the complete script, it begins with:

```text
Started: work-1
Away: doing other work, no subscription
```

“Step away” means leaving the subscription, while the service process and event loop keep running. `work-1` is an explicit demonstration identifier; in an application, omit `run_id` to let Runtime generate one.

## Come back for the result

Append the second block to the same `scheduling.py`. The entry point retains the Run handle and uses `wait()` to confirm a terminal state before collecting output. `wait()` makes the order of this experiment deterministic; it neither subscribes to output nor switches execution on.

```python
async def collect(run):
    parts = []
    cursor = 0
    subscription = run.subscribe(from_cursor=0)
    async for delivery in subscription:
        cursor = delivery.cursor
        if isinstance(delivery.event, TextDelta):
            parts.append(delivery.event.text)
            print(f"First read: {delivery.event.text.strip()} (replayed={delivery.replayed})")
            break
    await subscription.aclose()
    print(f"Saved cursor: {cursor}")
    async for delivery in run.subscribe(from_cursor=cursor):
        if isinstance(delivery.event, TextDelta):
            parts.append(delivery.event.text)
            print(f"Resumed: {delivery.event.text} (replayed={delivery.replayed})")
    print(f"Full result: {''.join(parts)}")
    assert ''.join(parts) == "First part. Second part."


async def main():
    runtime = Runtime(
        llm=PseudoLLMStreamClient(), model="offline-demo",
        database=InMemoryDatabase(), run_store=InMemoryRunStore(),
    )
    session = await runtime.load_session("scheduling-demo")
    run = await start_work(runtime, session)
    status = await run.wait()
    print(f"Back: {run.id}, status={status.value}")
    await collect(run)
    history = await runtime.load_session(session.session_id)
    print("Session history:")
    for message in history.messages:
        print(f"  {message['role']}: {message.get('content', '')}")
    assert [m['role'] for m in history.messages] == ['user', 'assistant']
    next_run = runtime.start(history, Query.text("Continue the report"), run_id="work-2")
    await next_run.wait()
    updated = await runtime.load_session(session.session_id)
    print(f"Continued: {next_run.id}, messages={len(updated.messages)}")
    assert len(updated.messages) == 4


asyncio.run(main())

```

Run it:

```sh
python scheduling.py
```

This is the complete output produced with `flops-agent==0.2.0` from PyPI:

```text
Started: work-1
Away: doing other work, no subscription
Back: work-1, status=done
First read: First part. (replayed=True)
Saved cursor: 1
Resumed: Second part. (replayed=True)
Full result: First part. Second part.
Session history:
  user: Prepare a short report
  assistant: First part. Second part.
Continued: work-2, messages=4
```

`subscribe(from_cursor=0)` replays from the beginning. The first subscription leaves after receiving text and saves that `Delivery.cursor`; the second passes that value back unchanged to read the remaining records. Do not calculate a cursor from the number of tokens or events received.

`load_session()` reloads conversation history: the two text parts have accumulated into one complete assistant message. Finally, a new Query in the same Session produces a new Run, `work-2`; the two exchanges total four messages. The offline LLM module always returns the same text, so this step verifies history accumulation and scheduling another execution.

This example deliberately retains the Run handle. `runtime.runs` is an active Run pool and removes entries after completion; `runtime.runs.get(run_id)` is not a historical lookup for finished work.

## Understand what just happened

- The task continued while you were away: `start()` had already scheduled Runner to execute; subscribing only observes output.
- The status was already `done` when you returned, yet you could collect the result: Run kept an event log, and its cursor let the next subscription continue from the saved position.
- Session retained the complete exchange: it is the shared conversation ledger across executions, and the next Run starts from that history.

This example replays the default in-memory log without event merging, so text is still readable as `TextDelta`. With custom log merging or serialization, replay may contain merged segments or serialized data; do not assume a one-to-one correspondence with live events.

## More scheduling patterns

- Start multiple Runs in different Sessions to advance them concurrently; another `start()` is rejected while the same Session has an active Run. See [Core concepts](02-core-concepts.md).
- Do other work after starting, and inspect a retained handle's `status` / `done` as needed. To stop work or wait for user input, see [Stop a task and accept user input](05-cancellation-and-suspension.md).
- Leave halfway through reading again, save the latest `Delivery.cursor`, and continue later. To keep sessions and logs beyond the process lifetime, see [Save and continue a session](04-sessions-and-persistence.md).
- Recovering work after a service restart needs recovery prerequisites and persistent records. See [Recover tasks after a process restart](06-recovery.md).

## Connect it to a product (HTTP + SSE)

A product can expose separate HTTP entry points for starting and subscribing: the start endpoint loads an authorized Session, calls `start()`, and immediately returns `run.id`; the subscription endpoint checks access and streams the corresponding Run's events to the browser. Leaving the page need not cancel the work.

The following only adapts the product's response body. It is **not a standalone Web service**; you must supply your own Web framework, routing, and authorization:

```python
async def sse_body(runtime, run, from_cursor=0):
    # The caller authorizes access and resolves the Run handle.
    async for frame in runtime.sse_stream(run, from_cursor=from_cursor):
        yield frame
```

Pass this async iterator to your Web framework's streaming response with content type `text/event-stream`. The browser saves the server-issued cursor and sends it back to the subscription endpoint after disconnecting; the product parses and validates it before passing it as `from_cursor`. Closing the response ends that subscription; deliberately stopping the Run requires a separate stop operation.

In 0.2.0, the default `WireCodec` injects a JSON `cursor` only for live, non-serialized frames. It does not add a cursor to replayed frames or automatically set SSE `id:`. If your product must support disconnecting again during replay, wrap the Delivery objects from `run.subscribe()` yourself, encode `cursor`, `replayed`, and output together, and define how the browser stores and returns them. The snippet above alone is not a complete reconnection protocol.

The product must also decide how long to retain Run handles, how to resolve identifiers and read historical logs, and how to route requests across processes. An HTTP connection does not provide those capabilities.

## Boundaries

- This example verifies starting, leaving a subscription, replaying, and continuing a conversation within one process. InMemory storage does not survive process exit and is not a durable task queue.
- `wait()` returns a terminal status, which does not necessarily mean success. Products should check `run.status` and inspect `run.error` on failure; the actual terminal status in this example is `done`.
- Conversation history and event logs are different records. This page uses both without covering storage backends, encryption, or recovery policy; those details belong in [04: Save and continue a session](04-sessions-and-persistence.md).

## Next

Read [Stop a task and accept user input](05-cancellation-and-suspension.md) to control work you have started and let it wait for a person's decision.

## Code evidence

These paths and line numbers refer to the PyPI `flops-agent==0.2.0` package installed for this example, relative to `site-packages/`:

- `flops_agent/engine/runtime.py:1041`: `start()` returns a handle immediately and schedules execution; it checks for an active Run in the same Session and removes the Run from the active pool after termination.
- `flops_agent/engine/execution.py:603`: `subscribe()` backfills history before live events and returns cursors through Delivery.
- `flops_agent/engine/execution.py:658`: `wait()` waits for terminal state and cleanup; status access starts at `:355`.
- `flops_agent/engine/runtime.py:961` and `flops_agent/entities/session.py:88`: loading Session and its message history.
- `flops_agent/engine/execution.py:163`: default logging without event merging.
- `flops_agent/engine/runtime.py:336` and `flops_agent/wire.py:170`: SSE adaptation and cursor encoding limits.
