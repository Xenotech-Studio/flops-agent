# How a user question is answered

After running [your first app](01-quick-start.md), follow one user question through the system: how input arrives, how the model calls tools, and how the response reaches the user. Runtime, Session, Query, and Run each appear where they take part in that flow.

## The question enters a conversation

When a user sends a message, the product calls `runtime.load_session(...)` with the conversation and owner identifiers to obtain its Session. The Session holds earlier messages so this turn can continue the conversation.

The product wraps the new input in `Query.text(text)`. A Query carries the input for this turn; the product still needs to pass it to Runtime to begin generating a response.

## Runtime starts the response

Runtime is usually assembled at product startup with the LLM module, tools, and storage configuration. The product calls `runtime.start(session, query)` to pass it the existing conversation and new question.

The call immediately returns a Run and advances execution in the background. The Run tracks this turn’s id, state, and output events; the product uses it to subscribe to progress, wait for the result, or request a stop. The Session continues to hold the conversation for later questions.

## The model answers and calls tools when needed

The Runner behind the Run adds the user input to the conversation and builds a model request from the message history and tool descriptions. The model can answer directly or request a tool call. Requests go through the LLM module supplied as `llm`; when integrating a service, replace the scripted stand-in with a real LLM module as described in [Connect your own model](connect-model.md).

When a tool is needed, Runner dispatches it, writes the result back to the conversation, and passes it to the model for further processing. One user question can therefore involve several model calls before the response is complete. If an Agent is connected, it can also supply identity, instructions, and memory; a first integration can use the default Runner without configuring an Agent.

Register tools through `tools` and replace the demo with your own business functions, as shown in [Give your agent a tool](07-extending.md); calls are handled by `executor`, which can remain a local executor or become a product-supplied remote adapter to suit deployment. When product policy needs to change the loop, configure a Runner subclass through `runner`, as described in [Put product policy at the right extension point](customize-runner.md).

For long-term memory, connect the optional `memory` on Agent to recall relevant content for model requests and schedule maintenance after normal completion; the product implements recall, maintenance, and storage, as described in [Connect long-term memory](long-term-memory.md).

## Output streams back during execution

As the model and tool loop advances, Runner turns output into events and sends them to the Run. The product receives events through `run.subscribe()` and presents text and progress to the user without waiting for the whole turn to finish.

For a browser client, the product also supplies an HTTP streaming endpoint and handles rendering. A subscription receives output while the Run tracks execution; one Run can have several subscribers or temporarily have none. The RunStore connected through `run_store` saves run logs and state; replace it with a persistent implementation when these records must survive a restart, as described in [Save a conversation and continue it](04-sessions-and-persistence.md).

## The turn ends and the conversation keeps its history

During execution and finalization, Runner writes conversation changes to the configured Database. Connect it through `database` and use a persistent backend when the product must retain conversations across restarts. After normal completion, `await run.wait()` returns `done`, allowing the product to finish waiting for this turn.

When the user asks another question, the product reloads the Session with the same conversation and owner identifiers, retrieves the existing messages, and starts a new Run for the new input. Each turn has its own execution state while sharing the same conversation history.

## Try it: two turns in one conversation

Save the following as `two_turns.py` at the repository root and run `python two_turns.py` in your existing virtual environment. The example uses the repository’s deterministic model to record a name and then send a second input in the same conversation.

```python
import asyncio
from docs.sample_product.server import ScriptedLLM
from flops_agent import Runtime, InMemoryDatabase, Query

async def main():
    runtime = Runtime(
        llm=ScriptedLLM([
            {"content": "I have recorded your name."},
            {"content": "Hello, Ming."},
        ]),
        database=InMemoryDatabase(),
    )
    for text in ["My name is Ming", "Please use my name"]:
        session = await runtime.load_session("demo", owner_id="local-user")
        run = runtime.start(session, Query.text(text))
        async for delivery in run.subscribe():
            print(delivery.event)
        print("Status:", (await run.wait()).value)
    session = await runtime.load_session("demo", owner_id="local-user")
    print("History roles:", [m["role"] for m in session.messages])

asyncio.run(main())
```

Both runs should end in `done`. The final history roles should contain two pairs of `user` and `assistant`, corresponding to the two inputs and responses.

## Boundaries

The example’s second response is scripted. It checks continuity of conversation history, not whether a real model understood that history. The in-memory storage used here also loses messages when the process restarts; retaining conversations across processes requires a persistent backend.

Do not start a second Run while the same Session already has an active one. Wait for completion or use [queued input](05-cancellation-and-suspension.md). Disconnecting a subscription does not automatically stop execution. A run can also stop, fail, or suspend, so the product must not treat every ending as a successful answer.

## Next steps

Next, read [Connect your own model](connect-model.md). With the runtime objects’ responsibilities clear, replace the example’s model stub with a real LLM module and verify requests and output.

If you want to jump directly to:

- [Scheduling work with Session and Run](03-streaming-and-sse.md) — Start work and collect its results later.
- [Glossary](glossary.md) — Look up terms for runtime objects and events.

## Code evidence

- `src/flops_agent/engine/runtime.py:961`, `:1041`: loading a Session and starting a Run.
- `src/flops_agent/engine/runner.py:315`: model and tool steps; `src/flops_agent/engine/execution.py:62`: the state set.
- `src/flops_agent/entities/query.py:37`: Query; `src/flops_agent/entities/agent.py:39`: Agent.
