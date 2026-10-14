# Build your first app

By the end of this tutorial, you will have a minimal agent app: it calls a tool you define, then replies using the tool result. You do not need an API key or a repository clone.

## Set up your environment

Start with Python 3.10 or later. In a Unix-like terminal, create a directory and virtual environment, then install the version verified for this tutorial from PyPI:

```sh
mkdir my-first-agent
cd my-first-agent
python3 -m venv .venv
. .venv/bin/activate
pip install "flops-agent[providers]==0.2.0"
```

The `providers` extra includes the HTTP client dependency for connecting a real model later.

## Write your app

Runtime assembles the app, and Runner drives the model and tool loop. Start with in-memory storage, then replace components as needed; Agent configures the role and instructions.

| Seam / configuration and responsibility | Used here | Used in a real product |
| --- | --- | --- |
| `agent`: role and instructions | `Agent()` | An Agent configured with a name and instructions |
| `llm`: model requests and streamed replies | `PseudoLLMStreamClient`, a scripted stand-in | A model client such as `OpenAIStreamClient` |
| `tools`: business capabilities available to the model | `get_record_status`, record status lookup by ID | Your business tools |
| `database`: save sessions and messages | `InMemoryDatabase`, process-local memory | Your chosen persistent backend |
| `run_store`: save run logs and state | `InMemoryRunStore`, process-local memory | A persistent backend implementing RunStore |
| `executor`: execute tool calls | Packaged `DefaultToolExecutor`, local execution | Keep it or use a remote executor adapter to suit deployment |
| `memory`: optional product seam | Not connected (default `None`) | Product-defined recall and maintenance; add later with [Connect long-term memory](long-term-memory.md) |
| `runner`: drive the loop | Packaged `Runner` | Keep it or subclass to customize product policy |
| `model`: model name passed to the client | `"scripted-model"`, a placeholder | A model name supported by your provider |

Create `app.py` and add the following three blocks in order. The first defines a business tool and a model stand-in; the stand-in requests the tool, then uses its result to compose a reply.

```python
import asyncio
from flops_agent import (
    Agent, InMemoryDatabase, InMemoryRunStore, Query, Runner, Runtime,
    TextDelta, TextStreamChunk, ToolCallStreamChunk, ToolResult,
)
from flops_agent.seams.executor import DefaultToolExecutor


def get_record_status(record_id: str) -> str:
    """Look up the status of a record."""
    statuses = {"record-1": "ready", "record-2": "pending"}
    return statuses.get(record_id, "unknown")


class PseudoLLMStreamClient:
    async def acompletion(self, **kwargs):
        async def stream():
            last = kwargs["messages"][-1]
            if last["role"] == "tool":
                yield TextStreamChunk(text=f"Result: {last['content']}")
            else:
                yield ToolCallStreamChunk(
                    index=0, id="status-1", name="get_record_status",
                    arguments_delta='{"record_id": "record-1"}',
                )
        return stream()
```

The second block assembles Runtime. `tools` registers the function and its parameter descriptions; `executor` decides how to execute it.

```python
def build_runtime():
    return Runtime(
        agent=Agent(),
        llm=PseudoLLMStreamClient(), tools=[get_record_status],
        database=InMemoryDatabase(), run_store=InMemoryRunStore(),
        executor=DefaultToolExecutor(), runner=Runner, model="scripted-model",
    )
```

The app components are now declared, but no request has started. In a product, assemble and reuse Runtime at process startup; the request entry point loads a Session, submits a Query, and reads the Run's events.

## Try it

Append the third block to the same `app.py`: this demonstration entry point creates a session, starts a run, and consumes its events.

```python
async def main():
    runtime = build_runtime()
    session = await runtime.load_session("my-first-app")
    run = runtime.start(session, Query.text("Please check the status of record-1"))
    async for delivery in run:
        event = delivery.event
        if isinstance(event, ToolResult):
            print("Tool result: ", event.result, sep="")
        elif isinstance(event, TextDelta):
            print("Model reply: ", event.text, sep="")


asyncio.run(main())
```

Save the file and run it in the same terminal:

```sh
python app.py
```

You will see this complete output, produced with `flops-agent==0.2.0` from PyPI:

```text
Tool result: ready
Model reply: Result: ready
```

`load_session()` loads or creates a Session, `Query.text()` carries the user request, and `start()` returns a Run for this execution. Runner calls the model stand-in, receives a tool request, and asks the executor to call `get_record_status()`; the first line is the tool's return value.

Runner writes the tool result back to the session, then calls `llm` again to generate the reply on the second line. The entry point consumes this run's events with `async for`, handling tool results and text deltas. `database` saves the session, and `run_store` saves run logs and state; these seams do not print separate lines.

## Next steps

Read [Connect your own model](connect-model.md) to replace `llm` and `model`; read [Save and continue a session](04-sessions-and-persistence.md) to connect persistent storage. To add business tools, read [Add tools to your agent](07-extending.md); for Runtime, Session, Query, and Run, read [Understand how tasks run](02-core-concepts.md).

## Boundaries

The scripted stand-in always queries `record-1`, does not interpret user input, and makes no real model request; the status lookup function only reads built-in demo data and returns `unknown` when a record is not found. Sessions and run records exist only in the current process; use persistent implementations according to your retention needs in production. Storage backends do not retain process-local Run handles, so do not rebuild Runtime for each streaming connection.

## Code references

These paths are relative to `site-packages/` in an installation of `flops-agent==0.2.0` from PyPI:

- `flops_agent/engine/runtime.py`: Runtime assembly, Session loading, and Run startup.
- `flops_agent/engine/runner.py`: the model and tool loop and result feedback.
- `flops_agent/seams/executor.py`, `flops_agent/tools/schema.py`: local execution and function tool registration.
- `flops_agent/seams/database.py`, `flops_agent/seams/run_store.py`: session and run storage interfaces and in-memory implementations.
- `flops_agent/entities/agent.py`, `flops_agent/entities/contracts.py`: Agent configuration and model stream chunks.
