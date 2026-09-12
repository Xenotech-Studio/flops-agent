# 1. Run an Agent in Five Minutes

Start with a working agent, then learn why it has these moving parts. The deterministic example included with the documentation needs no account, network connection, or model key. It demonstrates a text turn, a tool call, and agent memory without turning your first lesson into cloud configuration.

## Run the offline example

From the repository root:

\`\`\`bash
pip install -e ".[providers]"
python docs/sample_product/server.py
\`\`\`

The program prints SSE frames such as \`text_delta\`, tool start and completion, and \`loop_finished\`. \`server.py\` is deliberately an in-process server demo; it does not listen on a network port. Article 8 connects it to the separate executor and browser reference. For now, treat it as the runnable reference used throughout this guide.

## Install and call a real model

The package ships \`OpenAIStreamClient\`, which speaks the OpenAI chat-completions
streaming protocol (also DeepSeek, OpenRouter, local vLLM, ...). Install the
provider extra and set a key:

\`\`\`bash
pip install -e ".[providers]"
export DEEPSEEK_API_KEY='your-key'
\`\`\`

Create \`hello.py\`:

\`\`\`python
import asyncio
import os

from flops_agent import Runtime, OpenAIStreamClient


runtime = Runtime(
    llm=OpenAIStreamClient(
        os.environ["DEEPSEEK_API_KEY"],
        model="deepseek-chat",
        base_url="https://api.deepseek.com",
    ),
)


async def main() -> None:
    async for event in runtime.ask("Introduce yourself in one sentence."):
        text = getattr(event, "text", None)
        if text:
            print(text, end="", flush=True)


asyncio.run(main())
\`\`\`

Run \`python hello.py\`. Text appears incrementally. \`ask()\` is the convenient one-shot entry point: it creates a temporary \`Session\`, starts a \`Run\`, and hands events to you. It is ideal for a command line program or prototype. A browser or API service should use \`start()\` next, because it also needs a reconnection cursor and a cancellation handle.

With \`DEEPSEEK_API_KEY\` set, the same \`server.py\` also demonstrates a live model call. The scripted portion remains available, which makes the example useful as both a tutorial and a regression fixture.

## Turn the script into a service

Create one \`Runtime\` during application startup. Each request loads a session, starts a turn, and observes that turn:

\`\`\`python
from flops_agent import Contributor, InMemoryDatabase, Query, Runtime

runtime = Runtime(llm=my_llm, database=InMemoryDatabase(), tools=[get_weather])


async def chat(session_id: str, user_id: str, text: str):
    session = await runtime.load_session(session_id, owner_id=user_id)
    run = runtime.start(session, Query(content=text, by=Contributor.USER))
    async for delivery in run.subscribe():
        yield delivery
\`\`\`

\`my_llm\` only needs an \`async acompletion(**kwargs)\` method that returns an asynchronous chunk stream. \`get_weather\` may be an ordinary synchronous or asynchronous Python function; the framework derives its tool schema from the signature and invokes it through the default executor. \`load_session()\` creates an empty \`Session\` when one does not exist, so this is already a valid new-chat entry point.

You have now seen the framework's central shape: \`start()\` returns immediately; \`subscribe()\` observes work. The next article explains why this is not an ordinary async generator.

## The first common mistake

Do not construct \`Runtime(...)\` once per HTTP request. It owns reusable, stateless infrastructure configuration. Per-turn data belongs to \`Session\`, \`Query\`, and \`Run\`. Recreating a runtime per request defeats shared run tracking, cancellation wiring, and storage integration.

Next: [Core concepts](02-core-concepts.md).
