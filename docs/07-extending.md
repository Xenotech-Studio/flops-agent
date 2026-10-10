# Give your agent a tool

**Reader question:** How do I let the model call my function and verify that the result reaches the next request?

**Prerequisites:** Run [the basic two-turn example](02-core-concepts.md). **Outcome:** Register a tool, execute a deterministic call, and inspect its result in history.

## Try it

Save this as `tool_check.py` at the repository root and run `python tool_check.py`. A scripted model separates the question of whether a model chooses to call a tool from whether the tool is wired correctly.

```python
import asyncio
from docs.sample_product.server import ScriptedLLM
from flops_agent import Runtime, Session, Query, ToolResult

async def get_weather(city: str) -> dict:
    """Return sample weather without contacting a real weather service."""
    return {"city": city, "forecast": "sunny"}

async def main():
    llm = ScriptedLLM([
        {"tool_calls": [{"id": "weather-1", "name": "get_weather",
                         "arguments": {"city": "London"}}]},
        {"content": "The sample weather is sunny."},
    ])
    runtime = Runtime(llm=llm, tools=[get_weather])
    session = Session("tool-demo")
    run = runtime.start(session, Query.text("What is the weather in London?"))
    async for delivery in run.subscribe():
        if isinstance(delivery.event, ToolResult):
            print("Tool result:", delivery.event.result)
    print("History roles:", [m["role"] for m in session.messages])
    assert any(m["role"] == "tool" for m in session.messages)
    assert (await run.wait()).value == "done"

asyncio.run(main())
```

## Check the result

You should see a result containing city and forecast, and history roles `user -> assistant -> tool -> assistant`. This verifies registration, dispatch, and history writeback. The scripted final sentence does not prove that a model understands arbitrary tool results. After switching to a real model, separately verify that it can choose the tool and use its result.

## Define the argument boundary

Ordinary function schemas are derived from the signature, but the current minimal implementation declares parameters as strings. **It does not fully infer Python type annotations.** For numbers, enums, nested objects, or stricter constraints, pass `{"schema": schema, "fn": handler}` with an explicit OpenAI-style tool description.

A schema alone does not implement a tool. You also need a handler or executor route. Validate model-generated arguments inside your business handler; a schema does not replace user permissions or business authorization.

## When to extend further

If all you need is one function, stop here. For tool gates, identity and memory, or remote execution, read [Put product policy at the right extension point](customize-runner.md). The Runner, Memory, and Executor material formerly on this page is expanded there. Projection and summarization are consolidated in [Control model input](context-window.md).

ToolRegistry can manage tool packages, visibility, and execution routes. Introduce packages and capability filtering only after identifying a need that a flat tool list cannot meet. See the [API contract](api_surface.md).

## Next steps

Add one refusal condition and verify that a denial returns to the model without executing the tool. Follow [the product-policy article](customize-runner.md) to complete that exercise.

## Code evidence

- `src/flops_agent/tools/schema.py:17`, `:42`: accepted tool forms and string-parameter inference.
- `src/flops_agent/engine/runtime.py:173`: flat tools are registered in the default package.
- `src/flops_agent/engine/runner.py:523`: tool execution, interaction, and result writeback.
