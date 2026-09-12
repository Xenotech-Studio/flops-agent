"""Server/executor dispatch protocol: message vocabulary and constructors.

All messages are JSON dictionaries with a ``type``. Executors authenticate,
servers dispatch or cancel work, executors stream chunks/metadata and emit one
terminal result or failure, and servers acknowledge terminal messages. An
executor keeps terminal messages in its outbox until acknowledged and replays
them after reconnecting; server-side task-ID deduplication makes that safe.
See ``docs/sample_product/local_executor.py`` for a minimal executor.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable

AUTH = "auth"
AUTH_OK = "auth_ok"
AUTH_FAIL = "auth_fail"
RUN_TOOL = "run_tool"
CANCEL_TOOL = "cancel_tool"
STREAM_CHUNK = "stream_chunk"
STREAM_META = "stream_meta"
RESULT = "result"
FAILED = "failed"
RESULT_ACK = "tool_result_ack"

#: Executor-to-server message types handled by :meth:`link.ExecutorLink.receive`.
INBOUND_TYPES = frozenset({STREAM_CHUNK, STREAM_META, RESULT, FAILED})
#: Terminal message types.
TERMINAL_TYPES = frozenset({RESULT, FAILED})


# ── Server → executor ──────────────────────────────────────────────────────

def run_tool(task_id: str, tool_name: str, arguments: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
    """Dispatch one tool call; ``extra`` carries product-specific deployment data."""
    msg: Dict[str, Any] = {"type": RUN_TOOL, "task_id": task_id, "tool_name": tool_name, "arguments": arguments}
    msg.update(extra)
    return msg


def cancel_tool(task_id: str) -> Dict[str, Any]:
    """Interrupt an in-flight task."""
    return {"type": CANCEL_TOOL, "task_id": task_id}


def result_ack(task_id: str) -> Dict[str, Any]:
    """Acknowledge a terminal result so the executor can delete its outbox item."""
    return {"type": RESULT_ACK, "task_id": task_id}


def auth_ok(device_id: str, **extra: Any) -> Dict[str, Any]:
    msg: Dict[str, Any] = {"type": AUTH_OK, "device_id": device_id}
    msg.update(extra)
    return msg


def auth_fail(error: str) -> Dict[str, Any]:
    return {"type": AUTH_FAIL, "error": error}


# ── Executor → server ──────────────────────────────────────────────────────

def auth(device_id: str, device_name: str, capabilities: Iterable[str], **extra: Any) -> Dict[str, Any]:
    """Authenticate; ``capabilities`` lists capability labels and ``extra`` adds credentials."""
    msg: Dict[str, Any] = {
        "type": AUTH,
        "device_id": device_id,
        "device_name": device_name,
        "capabilities": list(capabilities),
    }
    msg.update(extra)
    return msg


def stream_chunk(task_id: str, chunk: str) -> Dict[str, Any]:
    return {"type": STREAM_CHUNK, "task_id": task_id, "chunk": chunk}


def stream_meta(task_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": STREAM_META, "task_id": task_id, "set": dict(fields)}


def result(task_id: str, value: Any) -> Dict[str, Any]:
    return {"type": RESULT, "task_id": task_id, "result": value}


def failed(task_id: str, error: str) -> Dict[str, Any]:
    return {"type": FAILED, "task_id": task_id, "error": error}


def task_id_of(message: Dict[str, Any]) -> str:
    """Return the message ``task_id`` as a stripped string, or an empty string."""
    raw = message.get("task_id")
    return str(raw).strip() if raw is not None else ""


def outcome_of(message: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a terminal message to the form needed for parking and replay."""
    return {
        "type": str(message.get("type") or ""),
        "task_id": task_id_of(message),
        "result": message.get("result"),
        "error": message.get("error"),
    }


def outcome_to_tool_result(outcome: Dict[str, Any]) -> Any:
    """Convert a terminal message to a tool result; nonterminal input returns None."""
    mtype = outcome.get("type")
    if mtype == RESULT:
        value: Any = outcome.get("result")
        return value if value else {"success": True}
    if mtype == FAILED:
        return {"success": False, "error": outcome.get("error") or "Task failed"}
    return None


__all__ = [
    "AUTH", "AUTH_OK", "AUTH_FAIL", "RUN_TOOL", "CANCEL_TOOL",
    "STREAM_CHUNK", "STREAM_META", "RESULT", "FAILED", "RESULT_ACK",
    "INBOUND_TYPES", "TERMINAL_TYPES",
    "run_tool", "cancel_tool", "result_ack", "auth_ok", "auth_fail",
    "auth", "stream_chunk", "stream_meta", "result", "failed",
    "task_id_of", "outcome_of", "outcome_to_tool_result",
]
