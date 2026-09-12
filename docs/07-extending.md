# 7. Extend the Framework: Tools, Runners, Memory, and Executors

Begin with the default Runtime. Replace a seam or override a clear Runner method
only when your product has a genuinely different policy or deployment boundary.
The general run lifecycle then stays in the framework instead of being rebuilt in
product code.

## Ordinary tools: minimal code

Pass an ordinary function:

    from flops_agent import Runtime

    async def get_weather(city: str) -> dict:
        return {"city": city, "forecast": "sunny"}

    runtime = Runtime(llm=my_llm, tools=[get_weather])

The framework derives a schema from the signature, invokes the function through
the default ToolExecutor, emits ToolResult, and stores the result in the session.
For complete schema control, pass an OpenAI-style schema or
{"schema": schema, "fn": handler}.

## Runner: where product policy belongs

Subclass Runner instead of copying its loop. A common safety gate overrides
before_tool():

    from typing import Any
    from flops_agent import Runner, ToolGate

    class SafeRunner(Runner):
        async def before_tool(self, call: Any) -> ToolGate:
            if call.function.name == "run_command" and "rm -rf" in call.function.arguments:
                return ToolGate.deny({"error": "Human approval is required."})
            return ToolGate.proceed()

    runtime = Runtime(llm=my_llm, tools=[run_command], runner=SafeRunner)

deny() does not run the tool, but returns a result to the model so it can choose
another route. rewrite() runs a changed call; suspend() suspends the current turn.
Other useful overrides are build_system_prompt() for persona and memory,
visible_tools() for the current tool projection, prepare_dispatch() to adjust
calls requested by the model, and after_execute() for post-tool interaction
policy.

When a step must not request the model at all, such as resuming a known tool call,
override plan_step() and return StepPlan.dispatch(calls). Return
StepPlan.finish(reason) to end immediately. Do not invent tools in
prepare_dispatch(); it only runs when the model already asked for a call.

## Agent and memory

Agent is the assistant's identity, not infrastructure. Give Runtime an agent with
instructions, a model preference, and Memory:

    from flops_agent import Agent, Runtime

    class Notes:
        async def recall(self, session, *, query=None) -> str:
            return "The user prefers concise answers."

        async def remember(self, session) -> None:
            save_summary_somewhere(session)

    agent = Agent(name="Assistant", instructions="Answer concisely.", memory=Notes())
    runtime = Runtime(llm=my_llm, agent=agent)

The framework calls recall() while building a request, then schedules remember()
after the run finishes. Memory maintenance cannot delay a response the user has
already seen. Storage, recall strategy, encryption, and retention are product
policy.

## Context compaction: project first, then write summaries

Long conversations cannot send every record to a model forever, but saving tokens
must not destroy factual history. Overriding Runner.project_messages() creates a
read-time projection: a temporary list for one request. To generate summaries,
use flops_agent.engine.compaction CompactionPolicy to plan coverage and write
CompactionRecord values into a product CompactionStore.

This is not a Runtime(compaction=True) switch. The product must provide model
window size, summary-model routing, CompactionStore, and the point at which a
summary may be written. The framework provides CJK-aware estimation, a recent
message floor, tool-call boundary alignment, quality checks, and planning. Keep
Session.messages as the auditable source of truth.

## Remote executors

For tools that run elsewhere, inject a ToolExecutor implementing
async execute(call, ctx). It may forward work over HTTP or WebSocket and use
ctx.stream_sink({"op": "append", ...}) for incremental output; the framework
converts it to ToolResultDelta. On stop, use a Run on_stop() callback to cancel
the remote task. On recovery, use ctx.record_dispatch() and ctx.resume_of to
avoid duplicate dispatch.

ToolContext includes run_id, tool_call_id, runtime, and session. A remote adapter
can record its task id and register a cancellation callback after confirming the
run remains local. Do not put WebSockets, device discovery, authentication tokens,
or product account objects into framework entities. Keep them in the executor
adapter or in product context passed to Runtime.start(...).

Tool catalogs, packages, capability filtering, and executor routes can live in
ToolRegistry. The next article maps these seams to the worked example. The
framework defines calls and lifecycle, not transport, device discovery, or account
authorization.

Next: [Worked example](08-worked-example.md).
