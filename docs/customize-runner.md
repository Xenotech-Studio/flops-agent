# Put product policy at the right extension point

**Reader question:** How can I add gates, memory, or remote execution without copying the entire run loop?

**Prerequisites:** Complete [the tool walkthrough](07-extending.md) and understand [stopping and suspension](05-cancellation-and-suspension.md). **Outcome:** Choose an extension boundary for your need and add a verifiable tool gate.

## Choose the smallest extension point

| Need | Extension point | Work left to the framework |
|---|---|---|
| Reject a tool before it runs | Runner.before_tool | The call loop, events, and history writeback |
| Give an assistant identity and knowledge across turns | Agent and Memory | Recall during requests and maintenance scheduling after completion |
| Execute tools in another process | ToolExecutor.execute | Call context, streaming events, and lifecycle |
| Change this request's model input | Runner.project_messages | Conversation facts and persistence flow |

## Try it: add a tool gate

Add this class to the previous article's example and change the Runtime constructor to `Runtime(llm=llm, tools=[get_weather], runner=WeatherOnlyRunner)`:

```python
from flops_agent import Runner, ToolGate

class WeatherOnlyRunner(Runner):
    async def before_tool(self, call):
        if call.function.name != "get_weather":
            return ToolGate.deny({"error": "This assistant only permits sample weather queries"})
        return ToolGate.proceed()
```

`deny()` skips execution but returns the refusal to the model, which can end the task or choose another approach. This example demonstrates the policy boundary, not a complete security system. Your product still implements user permissions, argument validation, and external-resource authorization.

## Check the result

First keep get_weather in the scripted request and confirm the original example still passes. Then change the requested tool name to a disallowed name. Verify that the handler is not called, history contains the denial result, and the run can finish. Gate behavior should be reproducible in a deterministic test rather than depend on whether a real model happens to request a tool.

## Add memory or remote execution

Memory defines `async recall(session, *, query=None)` and `async remember(session)`. The first returns text for this request; the second maintains the product's own knowledge store. Agent can carry instructions and memory. Memory maintenance is scheduled asynchronously after run completion, so do not treat completion as proof that it has already been persisted. Your product owns persistence and retries.

A remote executor implements `async execute(call, ctx)`. Send incremental output to ctx.stream_sink; forward Run's on_stop notification to the remote task system; correlate the original task during recovery with ctx.record_dispatch and ctx.resume_of. Keep HTTP/WebSocket connections, authentication, and device selection in the adapter, not in generic framework entities.

To execute a known call without asking the model again, override plan_step and return `StepPlan.dispatch(calls)`. To alter existing model calls, use prepare_dispatch. Do not expect a hook that runs only after the model has requested a call to create a step from nothing.

## Next steps

Connect the boundaries using [the product integration checklist](08-worked-example.md). For exact signatures, consult the [API contract](api_surface.md).

## Code evidence

- `src/flops_agent/engine/runner.py:523`, `:1383`: tool gates and default behavior; `:638`: step planning.
- `src/flops_agent/entities/agent.py:26`, `:79`: memory protocol and asynchronous maintenance.
- `src/flops_agent/seams/executor.py:1`, `src/flops_agent/tools/registry.py:49`: executor and context.
