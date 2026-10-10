# Understand how a task runs

**Reader question:** Which objects handled the example, and where should I send the next message?

**Prerequisites:** Complete [the five-minute guide](01-quick-start.md). **Outcome:** Explain the execution timeline and complete two turns in the same conversation.

## Start with four objects

| Object | The question it answers | Lifetime |
|---|---|---|
| Runtime | Which model, tools, and storage should do the work? | Usually assembled at product startup |
| Session | What has this conversation said so far? | Spans multiple runs; can be loaded from Database |
| Query | What new input does this turn contribute? | One user input, answer, or external event |
| Run | How far has this turn progressed, and how do I subscribe or stop it? | One execution, with its own id, state, and log |

Session is a conversation, not the currently executing task. Run is a task, not a browser connection. A Run can have multiple subscribers or temporarily have none.

## Follow the execution timeline

1. The product obtains a Session and constructs a Query from new input.
2. `runtime.start(session, query)` immediately returns a Run and starts work in the background.
3. Runner combines history and tool descriptions into a model request.
4. The model emits text or tool calls. Tool results enter history, and the model is called again if needed.
5. Run delivers events to subscribers and eventually completes, stops, fails, or suspends.

Runner executes the flow. Agent optionally supplies identity, instructions, and memory. You do not need to subclass Runner or configure Agent for your first integration.

## Try it: two turns in one conversation

Save the following as `two_turns.py` at the repository root and run `python two_turns.py` in your existing virtual environment. This still uses the repository's deterministic model. The second response is scripted; it does not demonstrate that a real model understood the history.

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

## Check the result

Both runs should end in `done`. The history roles should include two pairs of `user` and `assistant`. This checks continuity of history in the same conversation, not generation quality. Do not start a second Run while the same Session already has an active one. Wait for completion or use [queued input](05-cancellation-and-suspension.md).

## Next steps

If you have a real model, read [model integration](connect-model.md). To connect a browser, read [subscriptions and reconnection](03-streaming-and-sse.md). Use the [glossary](glossary.md) when you need a reminder of a term.

## Code evidence

- `src/flops_agent/engine/runtime.py:961`, `:1041`: loading a Session and starting a Run.
- `src/flops_agent/engine/runner.py:315`: model and tool steps; `src/flops_agent/engine/execution.py:62`: the state set.
- `src/flops_agent/entities/query.py:37`: Query; `src/flops_agent/entities/agent.py:39`: Agent.
