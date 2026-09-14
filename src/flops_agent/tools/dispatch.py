"""framework tool dispatch protocol: **package gate → route(server-side | executor) → invoke**.

``dispatch_tool`` is the "thin shell" framework part of ``execute_tool``: it only depends on
the registry protocol (TOOL_REGISTRY / get_tool_handler / get_unified_handler / kwargs_adapter /
DOMAIN_INFO), an injected ``ToolRouter``, and an already-built ``ToolContext``. **It contains no
product-level business logic** — tool-name compatibility, ``tool_domains`` resolution, redis
injection, ctx construction, etc. are all handled upstream by the product layer's ``execute_tool``
before calling this function. All implementation details of executor-side routing are hidden
behind the injected ``router.dispatch_to_executor``; the framework is unaware of them.
"""
from __future__ import annotations

import logging
import traceback
from typing import Optional

from flops_agent.entities.contracts import ToolArguments, ToolCall

from .registry import (
    DEFAULT_REGISTRY,
    ToolContext,
    ToolRouter,
    kwargs_adapter,
)

logger = logging.getLogger("flops_agent.tools.dispatch")


async def dispatch_tool(
    tool_call: ToolCall, arguments: ToolArguments, ctx: ToolContext, *,
    router: Optional[ToolRouter] = None,
) -> object:
    """package gate → route → invoke.

    - If the registry says this tool is registered to run on the executor
      (``register_tool(route=ROUTE_EXECUTOR)``), or the router's supplemental check says so:
      delegate entirely to ``router.dispatch_to_executor`` (guaranteed to return, never falls
      through to the registry).
    - Otherwise: resolve the tool within ``ctx.tool_domains`` (packages opened this turn) and
      dispatch through the unified ``(arguments, ctx)`` signature.
    - If registered for executor routing but no router is injected: return an error rather than
      mistakenly falling back to a local handler.
    """
    function_name = ctx.function_name
    tool_domains = ctx.tool_domains
    reg = ctx.registry or DEFAULT_REGISTRY

    # Routing decision belongs to the framework: the registry's route annotation is the primary
    # judge; the router's is_executor_routed is only a supplemental hook for the product layer.
    routed = reg.is_executor_routed(function_name)
    if not routed and router is not None:
        probe = getattr(router, "is_executor_routed", None)
        routed = bool(probe(function_name)) if probe is not None else False
    if routed:
        if router is None:
            return {
                "success": False,
                "error": f"Tool {function_name} is registered to run on the executor, but this process has no executor dispatch configured (ToolRouter).",
            }
        return await router.dispatch_to_executor(tool_call, arguments, ctx)

    # Look up the tool handler in the registry (only searching domains opened this turn; a
    # package that hasn't been opened means execution is refused).
    handler = None
    domain_hint = None
    try:
        domain_candidates = [d for d in (tool_domains or []) if isinstance(d, str) and d.strip()]  # pyright: ignore[reportUnnecessaryIsInstance] -- ToolContext.tool_domains's type is a contract; the constructor may not enforce it
    except Exception:
        domain_candidates = []

    # "Dispatchable" = has a raw _handler or a unified _uhandler (the latter supports
    # unified-only tools that only registered an adapter).
    for d in domain_candidates:
        h = reg.tool_handler(d, function_name)
        uh = reg.unified_handler(d, function_name)
        if h is not None or uh is not None:
            handler = h
            domain_hint = d
            break

    if domain_hint is None:
        # Tool isn't in any opened package: if the registry has it (via _handler or _uhandler),
        # hint the caller to open the corresponding package first; otherwise report unknown tool.
        required_domain = None
        for d, defs in reg.tools.items():
            if not isinstance(d, str) or not d.strip():  # pyright: ignore[reportUnnecessaryIsInstance] -- guards against the registry being populated in an unconventional way
                continue
            if function_name in defs and (
                defs[function_name].get("_handler") is not None
                or defs[function_name].get("_uhandler") is not None
            ):
                required_domain = d
                break
        if required_domain and required_domain != "/tools":
            pkg_info = reg.packages.get(required_domain, {})
            pkg_name = pkg_info.get("name") or required_domain
            return {
                "success": False,
                "error": f"Before using tool {function_name}, please call open_tool_packages to open the corresponding tool package first.",
                "required_package_path": required_domain,
                "required_package_name": pkg_name,
                "hint": f"Call open_tool_packages(package_paths=[\"{required_domain}\"]) to open \"{pkg_name}\" and then retry.",
            }
        result = {
            "success": False,
            "error": f"Unknown tool: {function_name} (tool_domains={tool_domains})",
        }
        logger.warning(f"Tool execution failed: {function_name}, error: {result['error']}")
        return result

    # Invoke the tool handler
    try:
        # Unified call protocol: migrated tools have a dedicated _uhandler attached; the rest are
        # wrapped on the fly with the generic kwargs_adapter into the same (arguments, ctx)
        # signature (injecting stream_sink as needed).
        _uhandler = reg.unified_handler(domain_hint, function_name)
        if _uhandler is None:
            if handler is None:
                # Theoretically unreachable: hitting domain_hint implies at least one of
                # handler / _uhandler is non-None
                return {"success": False, "error": f"Tool handler missing: {function_name}"}
            _uhandler = kwargs_adapter(handler)
        result = await _uhandler(arguments, ctx)
        logger.info(f"Tool executed successfully: {function_name}, result: {result}")
        return result
    except Exception as e:
        logger.error(f"Tool execution error: {function_name} (domain_hint={domain_hint}), error: {e}")
        traceback.print_exc()
        return {
            "success": False,
            "error": f"Tool execution error: {str(e)}",
        }


__all__ = ["dispatch_tool"]
