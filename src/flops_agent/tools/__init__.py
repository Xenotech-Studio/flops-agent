"""Tool registry and dispatch protocol for service-oriented agents.

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
    DEFAULT_REGISTRY,
    DOMAIN_INFO,
    PACKAGE_SYSTEM_PROMPTS,
    register_package,
    register_tool,
    get_tools_for_domain,
    get_tool_handler,
    register_unified_handler,
    get_unified_handler,
    get_available_domains,
    get_tool_catalog_json,
    get_opened_package_prompts,
)
from .schema import tool_call_to_openai, parse_tool_arguments

__all__ = [
    "ROUTE_EXECUTOR",
    "register_on_executor_package",
    "ToolContext",
    "ToolRouter",
    "dispatch_tool",
    "kwargs_adapter",
    "TOOL_REGISTRY",
    "DEFAULT_REGISTRY",
    "DOMAIN_INFO",
    "PACKAGE_SYSTEM_PROMPTS",
    "register_package",
    "register_tool",
    "get_tools_for_domain",
    "get_tool_handler",
    "register_unified_handler",
    "get_unified_handler",
    "get_available_domains",
    "get_tool_catalog_json",
    "get_opened_package_prompts",
    "tool_call_to_openai",
    "parse_tool_arguments",
]
