# Connect long-term memory

Add optional Memory when you need preferences across sessions, long-term facts, or personalization. First read [Save and continue a session](04-sessions-and-persistence.md): Database saves conversation history, RunStore saves run records, and Memory supplies context your product chooses to retain and recall. Continuing a session does not depend on Memory.

## Two methods and their call timing

The `Memory` protocol has only two asynchronous methods: `recall(session, *, query=None) -> str` and `remember(session) -> None`. `Agent.memory` defaults to `None`; the model and tool loop works without it.

The default Runner awaits `recall` through Agent while preparing a model request in `build_request()`. It incorporates the returned text into the system section, followed by projected history messages. This is neither a separate user message nor output intended for display. Recall is awaited on the request path; each default request build offers a read opportunity, so do not assume one read per Run.

When the main loop ends and enters finalization, default `finalize()` first persists the session, then calls `schedule_remember()` when the status is `DONE` and an Agent exists. A background task is created only if Memory exists, and that task awaits `remember`. Main Run completion does not wait for maintenance results. Scheduling happens before `run.finish()`, so the task may interleave with completion; this does not guarantee that the client has received the final frame.

Default `Agent.recall` catches exceptions, logs them, and returns an empty string. The background maintenance task also catches and logs exceptions without turning maintenance failure into reply failure. A custom Runner can override request preparation and finalization; directly calling a product read function does not automatically inherit Agent's exception fallback.

## A maintenance opportunity is not a write every turn

The following distinguishes framework defaults from Flops product practices; the latter are not mandatory configuration for every app:

| Layer | Scheduling or skip conditions |
| --- | --- |
| Default Runner / Agent | Schedules maintenance only for `DONE` with an Agent; no task is created without Memory. Failed, stopped, or suspended runs do not enter this maintenance path. |
| Flops product Runner | Also requires final conversation and message data to be in place. |
| Flops product Memory | The `FLOPS_MEMORY_STEWARD` switch can disable maintenance; available Redis, a bound agent, and usable user/assistant messages are also required. Disabling maintenance does not stop reads of existing memories. |
| Flops product distillation policy | Routing can skip maintenance or maintain only some layers; duplicate or unchanged data need not produce a write. The protocol does not require writing every N turns. |

`query` is an available parameter, not a mandatory retrieval strategy. Flops currently accepts it on the main recall path without using it for semantic retrieval; it reads that agent's long-term and recent memories. A product's on-demand retrieval tool is also distinct from automatic recall through the protocol.

## A minimal product integration sketch

This sketch uses the product convention of isolation by owner and bound agent, with explicitly injected read and maintenance functions. It is a structural illustration, not a complete runnable app; the product implements `read_context` and `maintain`, which are not framework APIs. The request entry point must set and validate `bound_agent_id` and access permissions; this meta field is not required by the framework.

```python
from copy import deepcopy
from flops_agent import Agent


class ProductMemory:
    def __init__(self, read_context, maintain, *, enabled=True):
        self.read_context = read_context
        self.maintain = maintain
        self.enabled = enabled

    async def recall(self, session, *, query=None):
        if session is None:
            return ""
        agent_id = session.meta.get("bound_agent_id")
        if not agent_id:
            return ""
        # Product function returns system context; query may filter relevance.
        return await self.read_context(session.owner_id, agent_id, query=query)

    async def remember(self, session):
        agent_id = session.meta.get("bound_agent_id")
        if not self.enabled or not agent_id:
            return
        messages = deepcopy([
            m for m in session.messages
            if m.get("role") in ("user", "assistant")
        ])
        if messages:
            # Already in a framework task; the product may skip unchanged data.
            await self.maintain(session.owner_id, agent_id, messages)


# Product supplies read_context / maintain; the entry point validates bound_agent_id.
# agent = Agent(memory=ProductMemory(read_context, maintain))
```

`read_context` should quickly return a string for the system section; the product decides how to degrade when storage is unavailable. `maintain` decides distillation, deduplication, writes, and skips; `enabled=False` disables maintenance while allowing reads. The sketch supplies neither pretend storage nor a distiller, and adds no completion signal outside the protocol.

It copies the user/assistant messages available in Session when the function executes, which may include earlier turns. `deepcopy` is only a local snapshot, not a Session frozen by the framework at Run completion; it cannot eliminate the window in which Session changes before the background task starts.

## Async and lifecycle boundaries

`maintain` must use genuinely asynchronous I/O or offload blocking library calls with `asyncio.to_thread`; declaring `async def` alone can still block the event loop. Using `await` inside the background task is normal; the framework's task scheduling is why the main Run does not wait for it.

The framework calls both methods. Do not manually call `remember` or `schedule_remember` after consuming a Run, as that would duplicate maintenance. The protocol has no `saved` and does not require printing maintenance results as evidence of this run's success. A standalone interface test may await `remember` directly, but that is a test invocation, not production request timing.

When a short script's `asyncio.run` exits, unfinished tasks are cancelled; it cannot promise memories were saved before exit. Run real updates in a long-lived app or an environment that explicitly manages maintenance lifecycles. This framework scheduling path guarantees neither a durable task queue, automatic retries, nor draining tasks at shutdown.

The product handles concurrency isolation, reliable task delivery, and failure recovery. Flops serializes maintenance with process-local locks grouped by owner and agent, and runs synchronous model calls in a thread; these are not cross-process locks. Maintenance may already have written some changes, so an exception does not imply transaction rollback.

## Framework and product responsibilities

The framework defines call signatures and default timing; the product decides where to store data, how to distill and encrypt it, and how to enforce capacity limits and handle conflicts. Flops's Redis storage, layered maintenance, and encryption guards are product implementations, not Memory protocol requirements.

In Flops, `MEMORY.md` and `MEMORY_RECENT.md` are logical paths mapped to storage fields, not ordinary Markdown files on a server. The main conversation injects long-term and recent content by default; daily memories and conversation impressions support later maintenance rather than all being injected directly. Deduplication uses text matching against selected system text, not semantic deduplication against all history or factual conflict resolution.

Define identity isolation, read latency, change detection, concurrency, and exit behavior for your product; saving chat records and distilling reusable knowledge are different responsibilities. After connecting the structure in this chapter, you still need to supply and verify actual read, maintenance, and persistence implementations.

## Next steps

Next, read [Add tools to an agent](07-extending.md). After learning how memory provides context, connect executable business functions so the agent can call your product’s capabilities.

## Code references

Framework paths are relative to the flops-agent repository; product paths are relative to the Flops repository. This chapter is based on local source inspection, not verification of production switches, write rates, or latency.

- `src/flops_agent/entities/agent.py`: Memory protocol, default empty configuration, recall exception fallback, and background scheduling.
- `src/flops_agent/engine/runner.py`: request building, system assembly, finalization, and Run completion ordering.
- `backend/agent/conversation_system/flops_memory.py`, `flops_runner.py`: product reads, maintenance conditions, and custom call paths.
- `backend/agent/memory_system/steward.py`, `steward_tasks.py`: maintenance stages, skips, thread offloading, and process-local serialization.
- `backend/agent/identity_system/storage.py`, `backend/agent/llm_system/prompts.py`, `backend/agent/memory_system/tool_guard.py`: logical paths, system text, and product storage guards.
