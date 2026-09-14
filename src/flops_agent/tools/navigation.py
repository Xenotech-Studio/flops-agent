"""Generic package-navigation registration mechanism.

Products supply action definitions and therefore own their tool names and wire
vocabulary. The framework only applies the resulting package-state change.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, cast

from .registry import DEFAULT_REGISTRY, ToolContext, ToolRegistry


async def _session_and_runtime(ctx: ToolContext) -> Any:
    """Return the call's session from ctx or load it through Runtime outside a run."""
    runtime = ctx.runtime
    if runtime is None:
        return None, {"success": False, "error": "package navigation requires Runtime (ctx.runtime is empty)"}
    session = ctx.session
    if session is None:
        session = await runtime.load_session(ctx.session_id, owner_id=ctx.user_id)
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


def _definition_name(definition: Dict[str, Any]) -> str:
    function: object = definition.get("function")
    name: object = cast(Dict[str, object], function).get("name") if isinstance(function, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise ValueError("package navigation definition requires a function name")
    return name


def register_package_navigation(
    registry: Optional[ToolRegistry] = None, *,
    open_definition: Dict[str, Any],
    close_definition: Dict[str, Any],
) -> None:
    """Register product-defined package open/close actions at the root package."""
    reg = registry if registry is not None else DEFAULT_REGISTRY
    open_name = _definition_name(open_definition)
    close_name = _definition_name(close_definition)
    reg.register_tool("/tools", open_name, open_definition)
    reg.register_unified_handler("/tools", open_name, _open)
    reg.register_tool("/tools", close_name, close_definition)
    reg.register_unified_handler("/tools", close_name, _close)


__all__ = ["register_package_navigation"]
