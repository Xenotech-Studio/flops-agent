"""Built-in root-navigation tools for opening and closing tool packages.

``open_tool_packages`` and ``close_tool_packages`` change only session state;
Runtime persists that state. Products may supply their own tool definitions.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .registry import DEFAULT_REGISTRY, ToolContext, ToolRegistry

OPEN_TOOL_PACKAGES_DEF: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "open_tool_packages",
        "description": "Open one or more tool packages (additively).",
        "parameters": {
            "type": "object",
            "properties": {
                "package_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tool-package paths to open, such as ['/tools/xxx']",
                }
            },
            "required": ["package_paths"],
        },
    },
}

CLOSE_TOOL_PACKAGES_DEF: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "close_tool_packages",
        "description": "Close one or more tool packages.",
        "parameters": {
            "type": "object",
            "properties": {
                "package_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tool-package paths to close.",
                }
            },
            "required": ["package_paths"],
        },
    },
}


async def _session_and_runtime(ctx: ToolContext) -> Any:
    """Return the call's session from ctx or load it through Runtime outside a run."""
    runtime = ctx.runtime
    if runtime is None:
        return None, {"success": False, "error": "open/close_tool_packages requires Runtime (ctx.runtime is empty)"}
    session = ctx.session
    if session is None:
        session = await runtime.load_session(ctx.conversation_id, owner_id=ctx.user_id)
    return (session, runtime), None


async def _open(arguments: Dict[str, Any], ctx: ToolContext) -> Any:
    got, err = await _session_and_runtime(ctx)
    if err is not None:
        return err
    session, runtime = got
    return runtime.open_packages(session, arguments.get("package_paths"))


async def _close(arguments: Dict[str, Any], ctx: ToolContext) -> Any:
    got, err = await _session_and_runtime(ctx)
    if err is not None:
        return err
    session, runtime = got
    return runtime.close_packages(session, arguments.get("package_paths"))


def register_navigation_tools(
    registry: Optional[ToolRegistry] = None, *,
    open_def: Optional[Dict[str, Any]] = None,
    close_def: Optional[Dict[str, Any]] = None,
) -> None:
    """Register both package toggles at ``/tools``; definitions may supply product copy."""
    reg = registry if registry is not None else DEFAULT_REGISTRY
    reg.register_tool("/tools", "open_tool_packages", open_def or OPEN_TOOL_PACKAGES_DEF)
    reg.register_unified_handler("/tools", "open_tool_packages", _open)
    reg.register_tool("/tools", "close_tool_packages", close_def or CLOSE_TOOL_PACKAGES_DEF)
    reg.register_unified_handler("/tools", "close_tool_packages", _close)


__all__ = ["OPEN_TOOL_PACKAGES_DEF", "CLOSE_TOOL_PACKAGES_DEF", "register_navigation_tools"]
