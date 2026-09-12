"""Local executor process: run tools on the user's own machine.

This independent process imports neither ``flops_agent`` nor the server. It
receives dispatches over WebSocket, executes them locally, streams output back,
and terminates work when it receives ``cancel_tool``. Run this file directly for
a self-contained demonstration.
"""
from __future__ import annotations

import asyncio
import subprocess
from typing import Any, Callable, Dict, List, Optional


# ── Executor: run a dispatch and allow cancel_tool to interrupt it ─────────

class LocalExecutor:
    """The half that runs on the user's machine."""

    def __init__(self):
        self._inflight: Dict[str, subprocess.Popen] = {}

    async def run_tool(self, msg: Dict[str, Any], *,
                       send: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
        """Handle one WebSocket dispatch; ``send`` is the return channel."""
        task_id = msg["task_id"]
        name = msg["name"]
        args = msg.get("args", {})
        if name != "run_command":
            return {"task_id": task_id, "error": f"unknown tool {name}"}

        command = args.get("command", "")
        proc = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        self._inflight[task_id] = proc
        stdout = ""
        try:
            # Stream each output line back through the return channel.
            assert proc.stdout is not None
            loop = asyncio.get_event_loop()
            while True:
                line = await loop.run_in_executor(None, proc.stdout.readline)
                if not line:
                    break
                stdout += line
                send({"tool_delta": task_id, "op": "append", "path": "stdout", "value": line})
            await loop.run_in_executor(None, proc.wait)
            return {"task_id": task_id, "ok": True, "stdout": stdout}
        finally:
            self._inflight.pop(task_id, None)

    def cancel_tool(self, task_id: str) -> None:
        """Terminate the subprocess for a received ``cancel_tool`` request."""
        proc = self._inflight.get(task_id)
        if proc is not None and proc.poll() is None:
            proc.terminate()


# ── Self-demo: command execution, streaming return, and interruption ───────

async def _demo() -> None:
    ex = LocalExecutor()
    print("== Execute a command and stream every output line ==")
    out = await ex.run_tool(
        {"task_id": "t1", "name": "run_command", "args": {"command": "printf 'a\\nb\\nc\\n'"}},
        send=lambda m: print("  -> WebSocket return:", m),
    )
    print("  terminal result:", out)

    print("\n== Interrupt a long-running command ==")
    task = asyncio.create_task(ex.run_tool(
        {"task_id": "t2", "name": "run_command", "args": {"command": "sleep 5; echo done"}},
        send=lambda m: print("  -> WebSocket return:", m),
    ))
    await asyncio.sleep(0.2)
    ex.cancel_tool("t2")            # Equivalent to receiving {cancel_tool: t2}.
    print("  terminal result:", await task)

    print("\nIn production this process connects to the server over WebSocket, receives "
          "{run_tool}/{cancel_tool}, and returns {tool_delta}. The server-side "
          "ExecutorAdapter feeds those messages into the ToolExecutor seam.")


if __name__ == "__main__":
    asyncio.run(_demo())
