"""FLOPS agent framework — tool registry & dispatch protocol.

Pure protocol layer: ToolContext, kwargs_adapter, register/get APIs,
TOOL_REGISTRY, catalog/prompt getters. Contains NO product copy
(DOMAIN_INFO / PACKAGE_SYSTEM_PROMPTS live in the product repo and are
injected at init time).
"""
from .dispatch import dispatch_tool
from .on_executor import register_on_executor_package
from .registry import (
    ROUTE_EXECUTOR,
    ToolContext,
    ToolRouter,
    kwargs_adapter,
    TOOL_REGISTRY,
    register_tool,
    get_tools_for_domain,
    get_tool_handler,
    register_unified_handler,
    get_unified_handler,
    get_available_domains,
    get_tool_catalog_json,
    get_opened_package_prompts,
)

__all__ = [
    "ROUTE_EXECUTOR",
    "register_on_executor_package",
    "ToolContext",
    "ToolRouter",
    "dispatch_tool",
    "kwargs_adapter",
    "TOOL_REGISTRY",
    "register_tool",
    "get_tools_for_domain",
    "get_tool_handler",
    "register_unified_handler",
    "get_unified_handler",
    "get_available_domains",
    "get_tool_catalog_json",
    "get_opened_package_prompts",
]
