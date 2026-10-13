# Build your first app

By the end of this tutorial, you will have written a working agent app from scratch: it calls a tool you define, then saves a memory. You do not need an API key or a repository clone.

## Set up your environment

Start with Python 3.10 or later and open a terminal. These commands use a Unix-like shell; installation needs an internet connection.

```sh
mkdir my-first-agent
cd my-first-agent
python3 -m venv .venv
. .venv/bin/activate
pip install "flops-agent[providers]==0.2.0"
```

These commands create your app directory and virtual environment, then install the tested version, `0.2.0`, from PyPI; the `providers` extra includes the HTTP client dependency for connecting a real model later.

## Write your app

Create `app.py` in this directory and add the following four blocks to the same file, in order.

```python
import asyncio

from flops_agent import (
    Agent, Query, Runtime, TextDelta, TextStreamChunk,
    ToolCallStreamChunk, ToolResult,
)


def delivery_fee(city: str) -> str:
    """Look up the delivery fee for a city."""
    return f"The delivery fee for {city} is 6 CNY"
```

This defines your business tool, `delivery_fee()`, with a simple rule for returning a delivery fee.

```python
class ScriptedModel:
    async def acompletion(self, **kwargs):
        async def stream():
            last = kwargs["messages"][-1]
            if last["role"] == "tool":
                yield TextStreamChunk(text=f"Result: {last['content']}")
            else:
                yield ToolCallStreamChunk(
                    index=0, id="fee-1", name="delivery_fee",
                    arguments_delta='{"city": "Shanghai"}',
                )
        return stream()
```

This supplies a model stand-in that needs no API key: it first requests `delivery_fee`, then uses the returned tool result to compose its reply.

```python
class NotebookMemory:
    def __init__(self):
        self.notes = []
        self.saved = asyncio.Event()

    async def recall(self, session, *, query=None):
        return "\n".join(self.notes)

    async def remember(self, session):
        user = next(m for m in reversed(session.messages) if m["role"] == "user")
        self.notes.append(user["content"])
        self.saved.set()
```

This implements the Memory interface, saving the latest user request in a list and using `saved` to signal that the write is complete.

```python
async def main():
    memory = NotebookMemory()
    runtime = Runtime(
        llm=ScriptedModel(), tools=[delivery_fee],
        agent=Agent(memory=memory),
    )
    session = await runtime.load_session("my-first-app")
    run = runtime.start(session, Query.text("Please check the delivery fee for Shanghai"))
    async for delivery in run:
        event = delivery.event
        if isinstance(event, ToolResult):
            print("Tool result: ", event.result, sep="")
        elif isinstance(event, TextDelta):
            print("Model reply: ", event.text, sep="")
    # Memory updates run in the background; wait for the write before reading.
    await asyncio.wait_for(memory.saved.wait(), timeout=5)
    print("Memory: ", memory.notes, sep="")


asyncio.run(main())
```

This assembles the Runtime, registers your tool, starts a Run, and prints the tool result and model reply before waiting for the memory write and printing the saved record.

## Try it

Save the file and run it in the same terminal:

```sh
python app.py
```

This runs the app you just wrote.

You will see:

```text
Tool result: The delivery fee for Shanghai is 6 CNY
Model reply: Result: The delivery fee for Shanghai is 6 CNY
Memory: ['Please check the delivery fee for Shanghai']
```

This is the complete output from running the code above with `flops-agent==0.2.0` installed from PyPI.

## Understand what happened

`runtime.load_session()` creates a Session, `Query.text()` carries the user request, and `runtime.start()` returns a Run for this execution. The default Runner then handles the loop of model calls, tool execution, and feeding results back to the model.

On the first model call, `ScriptedModel.acompletion()` supplies a `ToolCallStreamChunk` requesting `delivery_fee` with `{"city": "Shanghai"}`. Passing `tools=[delivery_fee]` registered your function as a tool, so the framework executes it. The first output line, `Tool result`, contains the function's return value.

The Runner appends the result to the Session as a `tool` message and calls the model again. The stand-in reads the last message's `content` and produces a `TextStreamChunk`, giving you the second line, `Model reply`. Change `6` to `8` in your function and run the app again: both lines will show a delivery fee of `8` CNY.

When the run completes normally, the framework schedules `NotebookMemory.remember()` to save the user request in `notes`. Memory updates happen in the background, so the app waits for `saved` before reading the list. The third line shows the record it wrote.

## Next steps

To use a real model, follow [Connect your own model](connect-model.md) and replace `llm=ScriptedModel()`. To save and resume conversations, follow [Save and continue a session](04-sessions-and-persistence.md) and connect a persistent Database; the list in your Memory implementation also needs persistent storage.

To add more business tools, read [Add tools to your agent](07-extending.md). For the relationship between Runtime, Session, Query, and Run, read [Understand how tasks run](02-core-concepts.md).

The 58 lines you just wrote are a working starting point. Replace `ScriptedModel` with a real model client and `NotebookMemory` with an implementation backed by persistent storage, and you can build your product on this code.

## Boundaries

The model here is scripted: it always queries Shanghai, does not interpret user input, and makes no real model request. The delivery fee function really executes, but returns a fixed demonstration fee; it does not query an external business system or execute system commands.

The Session and memory exist only in the current process and disappear when it exits. This Memory implementation only saves the original request; it does not distill memories or implement a retrieval strategy. Waiting for `saved` lets this short script confirm the write before exiting.

When integrating into a product, assemble and reuse the Runtime at process startup. Do not rebuild it for each streaming connection: a new instance cannot find the local run handles held by the original instance.

## Code references

These paths are relative to `site-packages/` in an installation of `flops-agent==0.2.0` from PyPI.

- `flops_agent-0.2.0.dist-info/METADATA:3`, `:19`, `:24`: package version, Python requirement, and the `providers` extra.
- `flops_agent/seams/llm_client.py:18`, `flops_agent/engine/runner.py:467`: the model seam's `acompletion()` is awaited to obtain an asynchronously iterable stream.
- `flops_agent/entities/contracts.py:51`, `:67`: text and tool-call stream chunks.
- `flops_agent/engine/runtime.py:147`, `:185`, `flops_agent/tools/schema.py:17`: Runtime construction and Python function tool registration.
- `flops_agent/engine/runtime.py:961`, `:1041`, `flops_agent/entities/query.py:63`: loading a Session, starting a Run, and creating a text Query.
- `flops_agent/engine/runner.py:539`, `:1388`: appending tool results to the Session and emitting result events.
- `flops_agent/entities/agent.py:26`, `:72`, `flops_agent/engine/runner.py:1423`: the Memory interface and background memory updates after normal completion.
- `flops_agent/engine/runtime.py:1`, `:1087`: Runtime reuse and local run handles.
