# Connect your own model

In [Build your first app](01-quick-start.md), `llm` was your `PseudoLLMStreamClient`, which scripted the model’s replies. This page replaces it with real calls: start with the packaged LLM module, then decide whether your provider’s interface needs your own implementation.

## Start with the packaged LLM module

Keep the virtual environment and `flops-agent[providers]==0.2.0` installation from 01; it includes the `httpx` used below. `OpenAIStreamClient` is the packaged streaming Chat Completions LLM module. When a provider exposes a compatible API, reuse it and change environment variables to switch providers or models.

Inject your provider-issued key into `MODEL_API_KEY` through the runtime environment, not source code. Set the remaining configuration like this, replacing the placeholders before running:

```sh
export MODEL_PROVIDER=openai-compatible
export MODEL_NAME=your-model-name
export MODEL_BASE_URL=https://your-endpoint.example/v1
```

`MODEL_BASE_URL` is the API root; the LLM module appends `/chat/completions`. The placeholder is not a working endpoint. Compatible endpoints must support the request fields and stream format used here; tool support needs separate verification.

First check connectivity. Save the following as `model_check.py` and run `python model_check.py` to check that a short reply arrives incrementally:

```python
import asyncio
import os
from flops_agent import Error, OpenAIStreamClient, Runtime, TextDelta

async def main():
    llm_module = OpenAIStreamClient(
        api_key=os.environ["MODEL_API_KEY"],
        model=os.environ["MODEL_NAME"],
        base_url=os.environ["MODEL_BASE_URL"],
    )
    async for event in Runtime(llm=llm_module).ask("Reply with one short sentence."):
        if isinstance(event, TextDelta):
            print(event.text, end="", flush=True)
        elif isinstance(event, Error):
            raise RuntimeError(event.message)
    print()

asyncio.run(main())
```

`Runtime.ask()` yields runtime events directly: `TextDelta.text` contains incremental text, and `Error.message` contains an error message. Missing environment variables cause `KeyError`; check your service configuration for authentication, model-name, or endpoint errors. Once connected, organize the LLM module into a reusable application module.

## Implement your own LLM module

Use the LLM seam when the provider has an incompatible API, requires a proprietary SDK, or needs your own retry, fallback, or per-request routing policy. The `PseudoLLMStreamClient` from 01 already fits this call shape. Replace its inner scripted stream with a real request and data conversion to create your own LLM module; subclassing the packaged implementation is unnecessary.

The method signature in PyPI 0.2.0’s `LLMStreamClient` is:

```python
async def acompletion(self, **completion_kwargs: object) -> AsyncIterator[StreamChunk]:
    ...
```

The framework first calls `await llm_module.acompletion(...)`, then asynchronously iterates the returned stream. The outer method therefore returns an async iterator, with `yield` inside an inner generator. Yield `StreamChunk` values such as `TextStreamChunk` or `ToolCallStreamChunk`; the runtime later converts them into events such as `TextDelta`. Do not return a complete string, raw network bytes, or runtime events.

Save this adaptable LLM module that calls a model over HTTP as `model_module.py`. To show where conversion happens, it uses an **illustrative endpoint contract**: send JSON containing fields such as `messages`, `tools`, and `model` to `/generate`; receive newline-delimited JSON (NDJSON), with one text or tool-call fragment per line and end-of-response marking the end of the stream. This is not a universal provider protocol. Adapt the request fields, authentication, and response conversion to your own API, or connect your SDK here.

The two response-line shapes are shown below; tool arguments arrive as string fragments:

```json
{"type":"text","text":"A short reply."}
{"type":"tool_call","index":0,"id":"call-1","name":"get_weather","arguments_delta":"{\"city\":\"Example City\"}"}
```

```python
import json
import os
from typing import AsyncIterator

import httpx
from flops_agent import (
    LLMStreamClient, OpenAIStreamClient, StreamChunk,
    TextStreamChunk, ToolCallStreamChunk,
)

class CustomLLMStreamClient:
    def __init__(self, *, api_key: str, model: str, base_url: str):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def acompletion(self, **completion_kwargs: object) -> AsyncIterator[StreamChunk]:
        request = dict(completion_kwargs)
        request.setdefault("model", self.model)

        async def stream() -> AsyncIterator[StreamChunk]:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/generate",
                    headers=headers, json=request,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        item = json.loads(line)
                        if item["type"] == "text":
                            yield TextStreamChunk(text=item["text"])
                        elif item["type"] == "tool_call":
                            yield ToolCallStreamChunk(
                                index=item["index"], id=item.get("id"),
                                name=item.get("name"),
                                arguments_delta=item.get("arguments_delta"),
                            )
                        else:
                            raise ValueError("Unsupported stream item type")
        return stream()
```

This minimal implementation preserves a request-level `model`, using the constructor’s model only when it is absent. Tool-call `index`, `id`, `name`, and `arguments_delta` must correspond to incremental data for the same call; do not turn tool calls into ordinary text. Network, HTTP status, and parsing failures propagate, and cancellation is not swallowed.

## One LLM module serves every session

The framework is designed for concurrent conversations, multiple agents, and multiple users. Construct Runtime once at application startup and share it across sessions; each Run has its own Runner. Construct the LLM module injected as `llm` once as well: different runs will call that same instance concurrently.

The LLM module must therefore be **free of per-request state**: never store a request's user, model, or key on `self`. Pass the model and credentials with each `acompletion(**kw)` call and use them only in that call's local variables. A router may hold stable configuration and reusable underlying LLM modules; “stateless” means it does not remember which user's request is current. This requirement also applies to those underlying LLM modules and is a prerequisite for sharing Runtime concurrently.

A factory selects the **implementation class** from the environment at startup; the resulting instance must meet this requirement. Append the factory below to `model_module.py`: without `MODEL_PROVIDER`, it selects the packaged implementation; `custom` selects your implementation. The URL, key, and default model here are stable deployment configuration. Never write a user's configuration back to these attributes while handling a request. When users need different credentials, the adapter must authenticate with credentials from the current call; the constructor-configured examples above still need that adaptation.

```python
def build_llm_module() -> LLMStreamClient:
    settings = dict(
        api_key=os.environ["MODEL_API_KEY"],
        model=os.environ["MODEL_NAME"],
        base_url=os.environ["MODEL_BASE_URL"],
    )
    provider = os.environ.get("MODEL_PROVIDER", "openai-compatible")
    if provider == "custom":
        return CustomLLMStreamClient(**settings)
    if provider == "openai-compatible":
        return OpenAIStreamClient(**settings)
    raise ValueError("Unsupported MODEL_PROVIDER")
```

The following demonstrates the standard calling pattern for a shared LLM module: each run carries its own model and credentials, and the LLM module routes using those parameters. Save it as `request_routing.py` and run `python request_routing.py` with `flops-agent==0.2.0`. `RouterClient` uses the prefix of `model` to select one of two simulated LLM modules, then forwards the remaining model name and the current credentials. The prefix is an application convention, not a routing feature built into the framework. Both requests share the same router and Runtime and run concurrently; the keys below are fake test values.

```python
import asyncio
from collections.abc import AsyncIterator
from flops_agent import (
    Error, LLMStreamClient, Query, Runner, Runtime, Session,
    StreamChunk, TextDelta, TextStreamChunk,
)

class DemoClient:
    def __init__(self, label: str):
        self.label = label

    async def acompletion(self, **kw: object) -> AsyncIterator[StreamChunk]:
        model, api_key = kw["model"], kw["api_key"]

        async def stream() -> AsyncIterator[StreamChunk]:
            await asyncio.sleep(0)
            assert api_key == f"demo-only-{self.label}"
            yield TextStreamChunk(text=f"{self.label}: {model}")

        return stream()

class RouterClient:
    def __init__(self):
        self.modules = {"fast": DemoClient("A"), "careful": DemoClient("B")}

    async def acompletion(self, **kw: object) -> AsyncIterator[StreamChunk]:
        route, separator, model = str(kw["model"]).partition("/")
        if not separator or not model or route not in self.modules:
            raise ValueError("Unknown model route")
        request = dict(kw)
        request["model"] = model
        return await self.modules[route].acompletion(**request)

class RoutedRunner(Runner):
    async def build_request(self):
        request = await super().build_request()
        request.update(model=self.context["model"], api_key=self.context["api_key"])
        return request

async def main():
    llm_module = RouterClient()
    assert isinstance(llm_module, LLMStreamClient)
    runtime = Runtime(llm=llm_module, runner=RoutedRunner)

    async def reply(session_id, model, api_key):
        run = runtime.start(
            Session(session_id), Query.text("Hello"),
            context={"model": model, "api_key": api_key},
        )
        parts = []
        async for delivery in run:
            event = delivery.event
            if isinstance(event, Error):
                raise RuntimeError(event.message)
            if isinstance(event, TextDelta):
                parts.append(event.text)
        return "".join(parts)

    results = await asyncio.gather(
        reply("one", "fast/small", "demo-only-A"),
        reply("two", "careful/large", "demo-only-B"),
    )
    assert results == ["A: small", "B: large"]
    print(*results, sep="\n")

asyncio.run(main())
```

```text
A: small
B: large
```

`RoutedRunner` bridges each run's `context` into completion parameters; `Runtime.start()` accepts that context, whereas `Runtime.ask()` has no per-request model argument. Each simulated LLM module checks its own request's fake key after yielding control, verifying that these two concurrent calls do not mix credentials. In a real application, the trusted application layer selects an allowed model and obtains its credentials; the underlying adapter uses them for authentication rather than placing them in messages or logs.

If request A writes user A's key to a shared instance's `self.api_key`, request B can overwrite it with user B's key while A is waiting. A may then resume and call the provider as B. This is why an LLM module must not retain the current request's state: the factory constructs the instance, and differences between calls belong in request parameters.

## Connect it to your app

Put `model_module.py` beside the `app.py` from 01 and replace its `build_runtime()` with the following. Call this assembly function once at application startup and reuse the returned Runtime and LLM module, rather than reconstructing them for each request. This code keeps deployment defaults; models and credentials that vary by run enter through the Runner request-building path above. Keep the other imports, `get_weather`, and entry point from 01:

```python
import os
from model_module import build_llm_module

def build_runtime():
    return Runtime(
        agent=Agent(),
        llm=build_llm_module(), tools=[get_weather],
        database=InMemoryDatabase(), run_store=InMemoryRunStore(),
        executor=DefaultToolExecutor(), runner=Runner,
        model=os.environ["MODEL_NAME"],
    )
```

This replaces both `llm=PseudoLLMStreamClient()` and `model="scripted-model"`. Runtime’s request-level model overrides the LLM module’s default, so the old placeholder must not remain. The original `PseudoLLMStreamClient` class is now unused and can be removed.

Run `python app.py` to check that the selected model requests the tool and uses its result in the answer. A real model need not reproduce the scripted output, and a successful text-only request does not establish tool-call compatibility. In your product, construct and reuse Runtime at startup and keep the `load_session()`, `start()`, and subscription path.

## Boundaries

Your product supplies credentials and configuration; the framework does not manage keys or discover credentials. Do not put keys in source code. The example URL is a placeholder: real calls require your own key and a working endpoint. Without a real key, you can verify construction, protocol adaptation, and local simulated calls, but cannot claim that a real provider has been verified.

The example LLM modules do not automatically retry or fall back. Provider retry, fallback, and routing policies belong to the product. Version 0.2.0 separately offers optional `Runtime.llm_stream_retry` to resend the current step when a stream call fails; it is disabled by default. It provides no cross-provider fallback and does not replace provider-specific LLM module policy. Avoid duplicated retries across layers; retries after output has begun also need to handle duplicate content.

The factory selects an implementation, and the router selects a destination per request. Both are application-defined; the framework has no built-in multi-provider router, but shared LLM modules must meet the concurrency requirement of retaining no per-request state. The custom interface illustrates only text and tool calls. Adapt reasoning, finish reasons, or usage fields to the provider’s actual responses as needed. Tool execution and authorization remain product responsibilities.

## Next steps

Next, read [Resume receiving results after disconnection](03-streaming-and-sse.md). Inject the verified LLM module into the Runtime your product keeps around, then connect run output to client subscriptions.

If you want to jump directly to:

- [Errors and limits](errors-and-limits.md) — Diagnose configuration and runtime failures.

## Code evidence

These paths and line numbers come from the PyPI installation of `flops-agent==0.2.0`, relative to `site-packages/`, rather than the current source branch:

- `flops_agent/seams/llm_client.py:17`: runtime-checkable `LLMStreamClient` protocol and method signature.
- `flops_agent/providers/openai.py:99`, `:116`: constructor arguments, request-level model override, and streaming request conversion.
- `flops_agent/entities/contracts.py:50`, `:66`: text and tool-call chunk fields.
- `flops_agent/entities/events.py:37`, `:179`: `TextDelta` and `Error` runtime events.
- `flops_agent/engine/runtime.py:147`, `:1218`: LLM module and model injection, optional retry configuration, and `ask()` event output.
- `flops_agent/engine/runner.py:392`: optional retry execution and cancellation propagation.
- `flops_agent/engine/runtime.py:1041`, `flops_agent/engine/runner.py:1038`: per-run context and completion request construction for the routing example.
