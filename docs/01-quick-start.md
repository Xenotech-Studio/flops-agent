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

Runtime brings together the app’s model client, tools, storage, and executor. This tutorial uses the package’s in-memory storage and local executor, plus the scripted stand-in below, to get the flow running. Once you see a reply, replace the components one at a time as needed.

You will encounter four of these parts here:

- `llm` → `PseudoLLMStreamClient` (a scripted stand-in; for a real model, see [Connect your own model](connect-model.md))
- `tools` → `get_weather` (your business tool; for more, see [Give your agent a tool](07-extending.md))
- `database / run_store` → in-memory implementations (for persistence, see [Save a conversation and continue it](04-sessions-and-persistence.md))
- `memory` → not connected (optional; see [Connect long-term memory](long-term-memory.md))

Create `app.py` and add the following three blocks in order. The first defines a business tool and a model stand-in; the stand-in requests the tool, then uses its result to compose a reply.

```python
import asyncio
from flops_agent import (
    Agent, InMemoryDatabase, InMemoryRunStore, Query, Runner, Runtime,
    TextDelta, TextStreamChunk, ToolCallStreamChunk, ToolResult,
)
from flops_agent.seams.executor import DefaultToolExecutor


def get_weather(city: str) -> str:
    """Look up the weather for a city."""
    return f"{city}: sunny"


class PseudoLLMStreamClient:
    async def acompletion(self, **kwargs):
        async def stream():
            last = kwargs["messages"][-1]
            if last["role"] == "tool":
                yield TextStreamChunk(text=f"Result: {last['content']}")
            else:
                yield ToolCallStreamChunk(
                    index=0, id="weather-1", name="get_weather",
                    arguments_delta='{"city": "Asgard"}',
                )
        return stream()
```

The second block assembles Runtime. `tools` registers the function and its parameter descriptions; `executor` decides how to execute it.

```python
def build_runtime():
    return Runtime(
        agent=Agent(),
        llm=PseudoLLMStreamClient(), tools=[get_weather],
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
    run = runtime.start(session, Query.text("Please check the weather in Asgard"))
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
Tool result: Asgard: sunny
Model reply: Result: Asgard: sunny
```

`load_session()` loads or creates a Session, `Query.text()` carries the user request, and `start()` returns a Run for this execution. Runner calls the model stand-in, receives a tool request, and asks the executor to call `get_weather()`; the first line is the tool's return value.

Runner writes the tool result back to the session, then calls `llm` again to generate the reply on the second line. The entry point consumes this run's events with `async for`, handling tool results and text deltas. `database` saves the session, and `run_store` saves run logs and state; these seams do not print separate lines.

## Next steps

Next, read [How a user question is answered](02-core-concepts.md). Follow the example you just ran to understand how Runtime, Session, Query, and Run relate before integrating a service.

If you want to jump directly to:

- [Connect your own model](connect-model.md) — Replace `llm` and `model`.
- [Save and continue conversations](04-sessions-and-persistence.md) — Connect persistent storage.
- [Add tools to an agent](07-extending.md) — Connect your own business tools.

## Boundaries

The scripted stand-in always queries the fictional city of Asgard, does not interpret user input, and makes no real model request; the weather function always returns "sunny" for the supplied city, does not connect to a real weather service, and includes no temperature or units. Sessions and run records exist only in the current process; use persistent implementations according to your retention needs in production. Storage backends do not retain process-local Run handles, so do not rebuild Runtime for each streaming connection.

## Code references

These paths are relative to `site-packages/` in an installation of `flops-agent==0.2.0` from PyPI:

- `flops_agent/engine/runtime.py`: Runtime assembly, Session loading, and Run startup.
- `flops_agent/engine/runner.py`: the model and tool loop and result feedback.
- `flops_agent/seams/executor.py`, `flops_agent/tools/schema.py`: local execution and function tool registration.
- `flops_agent/seams/database.py`, `flops_agent/seams/run_store.py`: session and run storage interfaces and in-memory implementations.
- `flops_agent/entities/agent.py`, `flops_agent/entities/contracts.py`: Agent configuration and model stream chunks.
