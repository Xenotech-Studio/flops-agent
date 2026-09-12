"""Product server process: assemble Runtime and expose chat/cancel endpoints.

This is one of three independent processes. It imports the installed
``flops_agent`` package, but not the local executor or frontend; the layers
communicate through HTTP/WebSocket rather than Python imports. It is a runnable
reference for product wiring, not a production HTTP service.
"""
from __future__ import annotations

from typing_extensions import override
import asyncio
import json
import os
from typing import Any, AsyncIterator, Dict, List, Optional

# This example deliberately imports the installed distribution. From a source
# checkout, run ``pip install -e .[providers]`` once before executing it.

from flops_agent import (
    ToolGate,  # noqa: E402
    Agent,
    Contributor,
    InMemoryDatabase,
    InMemoryRunStore,
    Query,
    Runner,
    Runtime,
    OpenAIStreamClient,
    ToolContext,
)


# ══════════════════════════════════════════════════════════════════════════
# 1. Tool catalog: the server owns the schema used in LLM requests
# ══════════════════════════════════════════════════════════════════════════
#
# The server defines each tool schema and the executor performs it. Both sides
# know ``run_command``; in production they are separate repositories that share
# only the tool name and argument shape.

TOOLS: List[dict] = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command on the executor machine.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
]


class SafetyInspector:
    """Recognize dangerous commands; production uses rules plus LLM review."""

    @staticmethod
    def inspect(command: str) -> str:
        return "block" if "rm -rf" in command else "allow"


# ══════════════════════════════════════════════════════════════════════════
# 2. Tool-execution seam: server-side adapter
# ══════════════════════════════════════════════════════════════════════════

class ExecutorAdapter:
    """Framework ToolExecutor implementation in the server process.

    A production adapter forwards calls over WebSocket to local_executor and
    routes output back through ``ctx.stream_sink``. This runnable example keeps
    execution inline while preserving the same streaming contract.
    """

    async def execute(self, call: Any, ctx: ToolContext) -> Any:
        args = json.loads(call.function.arguments or "{}")
        command = args.get("command", "")
        stdout = ""
        for seg in (f"$ {command}\n", "line-1\n", "(done)\n"):
            stdout += seg
            if ctx.stream_sink is not None:
                ctx.stream_sink({"op": "append", "path": "stdout", "value": seg})
            await asyncio.sleep(0)
        return {"ok": True, "stdout": stdout}


# ══════════════════════════════════════════════════════════════════════════
# 3. Product Runner: overrides express product policy
# ══════════════════════════════════════════════════════════════════════════

class SampleRunner(Runner):
    @override
    async def before_tool(self, call: Any) -> ToolGate:
        """Reject commands that fail review and return the result to the model."""
        args = json.loads(call.function.arguments or "{}")
        if call.function.name == "run_command":
            if SafetyInspector.inspect(args.get("command", "")) == "block":
                return ToolGate.deny({"error": "blocked", "need_user_confirm": True})
        return ToolGate.proceed()


# ══════════════════════════════════════════════════════════════════════════
# 4. LLM / Memory / RunStore seams: product implementations
# ══════════════════════════════════════════════════════════════════════════

class ScriptedLLM:
    """Deterministic streaming test double that replays a fixed script."""

    def __init__(self, script: List[Dict[str, Any]]):
        self._script, self._i = script, 0

    async def acompletion(self, **request) -> Any:
        turn = self._script[self._i]
        self._i += 1
        return _ScriptedStream(turn)


class _ScriptedStream:
    def __init__(self, turn: Dict[str, Any]):
        self._chunks, self._i = _turn_to_chunks(turn), 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        c = self._chunks[self._i]
        self._i += 1
        return c


def _turn_to_chunks(turn: Dict[str, Any]) -> list:
    from types import SimpleNamespace as SN

    def _chunk(**delta):
        return SN(choices=[SN(delta=SN(**{"content": None, "reasoning_content": None,
                                          "tool_calls": None, **delta}),
                              finish_reason=None, index=0)], usage=None)
    out = [_chunk(content=p) for p in turn.get("chunks", [])]
    for tc in turn.get("tool_calls", []):
        out.append(_chunk(tool_calls=[SN(index=0, id=tc["id"], type="function",
                                         function=SN(name=tc["name"],
                                                     arguments=json.dumps(tc["arguments"])))]))
    if turn.get("content"):
        out.append(_chunk(content=turn["content"]))
    return out


class NotebookMemory:
    """Example agent memory; the product chooses its storage and distillation."""

    def __init__(self):
        self.notes: List[str] = []

    async def recall(self, session: Any, *, query: Any = None) -> str:
        return "\n".join(f"- {n}" for n in self.notes)

    async def remember(self, session: Any) -> None:
        last = next((m["content"] for m in reversed(session.messages)
                     if m.get("role") == "user"), "")
        if last:
            self.notes.append(f"User said: {last}")


# ══════════════════════════════════════════════════════════════════════════
# 5. Endpoints: chat, cancellation, and startup recovery
# ══════════════════════════════════════════════════════════════════════════

def build_runtime(llm: Any, agent: Optional[Agent] = None) -> Runtime:
    """Assemble all seams once at product startup."""
    return Runtime(
        agent=agent,
        llm=llm,
        tools=TOOLS,
        database=InMemoryDatabase(),      # Production: Redis cache-aside plus SQLite.
        run_store=InMemoryRunStore(),     # Production: replace with Redis.
        executor=ExecutorAdapter(),       # Production: WebSocket proxy to local_executor.
        runner=SampleRunner,
        model="sample-model",
    )



async def handle_chat_request(runtime: Runtime, session_id: str, text: str,
                              *, owner_id: str = "u1") -> AsyncIterator[str]:
    """Start a run for a chat request and subscribe to its SSE stream."""
    session = await runtime.load_session(session_id, owner_id=owner_id)
    # This compact example supports only a new user message; product request
    # routing for retry, continuation, and recovery remains product-specific.
    query = Query(content=text, by=Contributor.USER)
    run = runtime.start(session, query, context={"owner_id": owner_id})
    # A production endpoint can emit a prelude frame here (v2_run/run_id).
    async for line in runtime.sse_stream(run):
        yield line          # Standard body: runtime.wire serialization plus replay cursors.


async def handle_cancel(runtime: Runtime, session_id: str, *, owner_id: str = "u1") -> Dict[str, Any]:
    """Ask the runtime to stop the active run for this session."""
    run_id = await runtime.stop_session(session_id, owner_id=owner_id)
    if run_id is not None:
        return {"stopped": True, "run_id": run_id}
    return {"stopped": False, "note": "no active run"}


async def on_startup(runtime: Runtime) -> int:
    """Resume interrupted runs when the process starts."""
    async def _resume(meta):
        pass   # Rebuild the session, keys, and request entrypoint here.

    return await runtime.recover(_resume, on_gave_up=lambda meta: None)


# ══════════════════════════════════════════════════════════════════════════
# 6. Run it (in-process demo; production connects local_executor and frontend)
# ══════════════════════════════════════════════════════════════════════════

async def _demo(title: str, llm, text, agent=None):
    print(f"\n{'=' * 66}\n{title}\n{'=' * 66}")
    runtime = build_runtime(llm, agent=agent)
    async for line in handle_chat_request(runtime, "demo", text):
        print("  " + line.strip())


async def main() -> None:
    # Use the bundled client for a live DeepSeek request when a key is present.
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        await _demo("Live DeepSeek", OpenAIStreamClient(key, model="deepseek-chat", base_url="https://api.deepseek.com"), "Introduce yourself in one sentence.")
    else:
        print("(Set DEEPSEEK_API_KEY for a live DeepSeek request; using scripted demos below.)")

    # Scripted doubles make tool calls, safety gates, and memory deterministic.
    await _demo("Safe tool (scripted)", ScriptedLLM([
        {"tool_calls": [{"id": "c1", "name": "run_command", "arguments": {"command": "ls"}}]},
        {"content": "The directory has been listed."},
    ]), "List the directory")
    mem = NotebookMemory()
    agent = Agent(id="a1", name="Assistant", instructions="Be concise.", memory=mem)
    await _demo("Persona and memory (scripted)", ScriptedLLM([{"chunks": ["Understood."]}]), "My name is Ming", agent=agent)
    await asyncio.sleep(0.05)
    print(f"    memory: {mem.notes}")
    print("\nIn production, connect this process to an HTTP/WebSocket server, then "
          "to local_executor and frontend.html. The three communicate only by wire.")


if __name__ == "__main__":
    asyncio.run(main())
