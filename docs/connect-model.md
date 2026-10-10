# Connect your own model

**Reader question:** How do I replace the offline model with a real service and verify the connection?

**Prerequisites:** Complete [the execution walkthrough](02-core-concepts.md) and obtain access to a service with a compatible streaming Chat Completions endpoint. **Outcome:** Complete a request through your endpoint and distinguish network failures from runtime failures.

## Prepare the configuration

The `.[providers]` installation in the quick start already includes the HTTP client. To install only the released package, use `python -m pip install 'flops-agent[providers]==0.2.0'`. Your product supplies the API key, model name, and base URL. The framework does not search the machine for configuration files.

The program below reads `MODEL_API_KEY`, `MODEL_NAME`, and `MODEL_BASE_URL` from the environment. Set real values in your own runtime environment; do not put credentials in source code. Use the service's API root as the base URL, such as an address ending in `/v1`. Do not append `/chat/completions`: the client adds it.

## Try it

Save this as `model_check.py`, then run `python model_check.py`:

```python
import asyncio
import os
from flops_agent import OpenAIStreamClient, Runtime, TextDelta, Error

async def main():
    client = OpenAIStreamClient(
        api_key=os.environ["MODEL_API_KEY"],
        model=os.environ["MODEL_NAME"],
        base_url=os.environ["MODEL_BASE_URL"],
    )
    runtime = Runtime(llm=client)
    async for event in runtime.ask("Introduce yourself in one sentence."):
        if isinstance(event, TextDelta):
            print(event.text, end="", flush=True)
        elif isinstance(event, Error):
            raise RuntimeError(event.message)
    print()

asyncio.run(main())
```

`ask()` yields events directly and suits one-off scripts. To obtain a run_id, cursor, or stop handle, or to continue a conversation, use the previous article's `load_session()` -> `start()` -> `subscribe()` path.

## Check the result

You should see a sentence arrive incrementally. A startup `KeyError` means an environment variable is missing. For authentication or unknown-model errors, check the service configuration first. For incompatible stream formats, check that the service actually provides compatible incremental responses. Text streaming support does not imply tool-call support; verify that separately with [the tool walkthrough](07-extending.md).

If you already have a custom client, its `async acompletion(**request)` must return an asynchronously iterable stream of chunks. Do not return a complete string or treat raw SSE bytes as framework events. Use LLMStreamClient and the existing provider adapter as references for boundary conversion.

## Next steps

Inject the verified client into the Runtime your product keeps around, then connect [client subscriptions](03-streaming-and-sse.md). For failures, consult [errors and limits](errors-and-limits.md).

## Code evidence

- `src/flops_agent/providers/openai.py:99`, `:116`: constructor arguments, streaming requests, and endpoint construction.
- `src/flops_agent/seams/llm_client.py:18`: client protocol.
- `src/flops_agent/engine/runtime.py:1218`: the events yielded by ask.
