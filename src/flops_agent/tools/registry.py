"""Tool registry -- a catalog of tools grouped by path (protocol layer / framework).

A :class:`ToolRegistry` holds three things:

* **Packages** (``register_package``): a path, name, description, and an optional package
  prompt (injected into system when the package is opened). A "group package" carrying no
  tools of its own can also be registered -- its value is a summary line plus the sub-package
  index it exposes once opened.
* **Tools** (``register_tool``): a litellm tool definition + handler attached under a package
  path; ``register_unified_handler`` attaches a unified ``(arguments, ctx)`` signature adapter
  to it.
* **Views**: catalog JSON, the list of openable packages, prompts for already-opened packages --
  all read-only projections over the two items above.

The registry is an object owned by ``Runtime`` (``Runtime(registry=…)``); two Runtimes in the
same process can each use their own. ``DEFAULT_REGISTRY`` is the module-level default instance;
module-level functions like ``register_tool``, along with the three names ``TOOL_REGISTRY`` /
``DOMAIN_INFO`` / ``PACKAGE_SYSTEM_PROMPTS``, all point to it, saving effort for product layers
that only need a single instance. **Contains no product-level copy**: package names,
descriptions, and prompts are all registered by the product layer.
"""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol, Set, cast, runtime_checkable

from flops_agent.entities.contracts import DispatchRecord, ToolArguments, ToolCall, ToolOutcome

logger = logging.getLogger(__name__)


#: A value of ``register_tool(route=…)`` that the framework recognizes: the tool runs on a
#: remote executor (delivered via ToolRouter).
ROUTE_EXECUTOR = "executor"


# ---------------------------------------------------------------------------
# Unified tool call protocol (framework dispatch protocol)
#
# Goal: the dispatcher recognizes only one handler signature --
# ``handler(arguments: dict, ctx) -> dict`` -- and no longer holds the parameter shape of any
# specific tool. ctx carries only "protocol-level" request data and never any business
# (Flow-family) fields; business context (flowdoc config / LLM keys, etc.) is resolved by the
# product layer's registered adapter itself and never passes through the framework.
# ---------------------------------------------------------------------------
@dataclass
class ToolContext:
    """Protocol-level tool call context. Holds only request-scoped generic data, so it can live
    entirely in the framework.

    The product layer's adapter takes protocol fields such as user_id / conversation_id /
    stream_sink from here; business config (e.g. flowdoc base_url, litellm key) is resolved by
    the adapter calling product helpers directly, and is not put into ctx -- keeping the
    framework free of business-logic contamination.
    """
    user_id: str
    conversation_id: str
    function_name: str
    tool_domains: List[str]
    client_ip: Optional[str] = None
    stream_sink: Optional[Callable[[Dict[str, Any]], None]] = None
    authorization: Optional[str] = None
    request_id: Optional[str] = None
    #: The registry used for this dispatch (``Runtime.registry``). None = the module default instance.
    registry: Optional["ToolRegistry"] = None
    #: The session and runtime for this run (framework tools such as package open/close need to
    #: mutate session state and persist it). May be None for calls made outside a run.
    session: Optional[Any] = None
    runtime: Optional[Any] = None
    #: The run this call belongs to, and the LLM-generated call id (the key used for dispatch
    #: bookkeeping). None for calls made outside a run.
    run_id: Optional[str] = None
    tool_call_id: Optional[str] = None
    #: Resume dispatch: a prior process already dispatched this call once; this is the dispatch
    #: record registered at that time (including remote task ids etc. appended by the executor
    #: via :meth:`record_dispatch`). None = a normal first-time dispatch.
    #: The remote executor uses this to reuse the old task id instead of resending -- resending
    #: would mean duplicate execution.
    resume_of: Optional[DispatchRecord] = None

    def record_dispatch(self, **fields: object) -> None:
        """Merge dispatch facts (remote task_id, target device, etc.) into this call's dispatch record.

        If the process dies during tool execution, the resumed run retrieves these fields via
        ``resume_of``. Silently no-ops when there's no run / the store doesn't support
        bookkeeping -- bookkeeping is a side channel and shouldn't affect the call itself.
        """
        store = getattr(self.runtime, "run_store", None) if self.runtime is not None else None
        recorder = getattr(store, "record_dispatch", None)
        if recorder is None or not self.run_id or not self.tool_call_id:
            return
        try:
            recorder(self.run_id, self.tool_call_id, dict(fields))
        except Exception:
            logger.warning("record_dispatch failed run=%s call=%s", self.run_id, self.tool_call_id, exc_info=True)


def kwargs_adapter(fn: Callable[..., Any]) -> Callable[[Dict[str, Any], "ToolContext"], Any]:
    """Wrap a business handler that "takes **kwargs keyed by arguments" into the unified
    ``(arguments, ctx)`` signature.

    Behavior mirrors the legacy dispatcher's generic branch: when arguments is a dict it is
    expanded into kwargs; ctx.stream_sink is injected only when fn's signature includes
    ``stream_sink`` or ``**kwargs``, so unrelated tools don't error out on an unexpected extra
    argument. When arguments is not a dict, fn is called with a single positional argument
    (the legacy ``handler(arguments)`` style).
    """
    async def _adapter(arguments: Dict[str, Any], ctx: "ToolContext") -> Any:
        if isinstance(arguments, dict):  # pyright: ignore[reportUnnecessaryIsInstance] -- the type annotation is a contract; actual callers may violate it (see the fn(arguments) branch below)
            kwargs = dict(arguments)
            if ctx.stream_sink is not None:
                try:
                    sig = inspect.signature(fn)
                    if "stream_sink" in sig.parameters or any(
                        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                    ):
                        kwargs["stream_sink"] = ctx.stream_sink
                except (ValueError, TypeError):
                    pass
            result: Any = fn(**kwargs)
        else:
            result = fn(arguments)
        if inspect.isawaitable(result):          # Synchronous functions can also serve as tools (the flat tools=[fn] style)
            result = await result
        return result

    return _adapter


@runtime_checkable
class ToolRouter(Protocol):
    """The product layer's "remote executor" delivery implementation (the routing exit point of
    framework dispatch).

    **Which tool belongs to the executor is answered by the registry** (the ``route``
    annotation at tool registration time, see :meth:`ToolRegistry.is_executor_routed`); this
    protocol is only responsible for "how to deliver it": the WS protocol, device
    discovery / online status / capability validation, run_tool dispatch and streaming result
    relay, etc. are all hidden behind ``dispatch_to_executor``; the framework is unaware of them.

    ``is_executor_routed`` is an **additional** (optional) check made by the product layer: when
    the registry says a tool doesn't belong to the executor, it's asked once more, leaving a
    hook for legacy tools not yet registered in the registry. If no router is injected (None)
    but a tool is still marked for executor routing, dispatch returns an error directly --
    it never mistakenly falls back to a local handler.
    """

    def is_executor_routed(self, function_name: str) -> bool:
        """(Optional) An additional check beyond the registry: should this tool also go to the executor."""
        ...

    async def dispatch_to_executor(
        self, tool_call: ToolCall, arguments: ToolArguments, ctx: "ToolContext",
    ) -> ToolOutcome:
        """Dispatch the tool to the executor and return the result (protocol guarantee: an
        executor-routed tool always returns here and never falls back to the registry). Required
        request context (function_name / tool_domains / user_id / conversation_id / stream_sink)
        is taken from ctx."""
        ...


class ToolRegistry:
    """A tool catalog: packages (path -> name/description/prompt) + tools (path -> name -> definition and handler)."""

    def __init__(self) -> None:
        #: Package metadata: path -> {"name", "description"}. Both catalog display and the
        #: "openable packages" list come from here.
        self.packages: Dict[str, Dict[str, Any]] = {}
        #: System-prompt snippet for each package: injected into system when the package is
        #: opened. Keyed by path.
        self.package_prompts: Dict[str, str] = {}
        #: Tools: path -> tool_name -> litellm tool definition (includes ``_handler`` /
        #: ``_uhandler`` / ``_requires`` / ``_hidden_when``; underscore-prefixed keys never go
        #: on the wire).
        self.tools: Dict[str, Dict[str, Dict[str, Any]]] = {}
        #: Package-level capability requirements: path -> list of tags. If the product layer's
        #: current capabilities (``Runner.available_capabilities``) miss any tag, the whole
        #: package is invisible (e.g. "the executor package only shows up when an executor is online").
        self.package_requires: Dict[str, List[str]] = {}

    # -- Registration --------------------------------------------------------
    def register_package(
        self, path: str, *, name: str, description: str, system_prompt: Optional[str] = None,
        requires: Optional[List[str]] = None,
    ) -> None:
        """Register a package (path, name, description, optional prompt, optional capability requirements). Re-registering overwrites."""
        self.packages[path] = {"name": name, "description": description}
        if system_prompt is not None:
            self.package_prompts[path] = system_prompt
        if requires is not None:
            self.package_requires[path] = list(requires)

    def register_tool(
        self, domain_path: str, tool_name: str, tool_def: Dict[str, Any],
        handler: Optional[Callable[..., Any]] = None, *,
        requires: Optional[List[str]] = None, hidden_when: Optional[List[str]] = None,
        route: Optional[str] = None,
    ) -> None:
        """Attach a litellm tool definition under ``domain_path``; ``handler`` is the execution function (optional).

        ``requires``: if the product layer's capabilities miss any of these tags, the tool is
        invisible (e.g. requiring some executor capability);
        ``hidden_when``: if the product layer's capabilities contain any of these tags, the tool
        is redundant and invisible (e.g. don't offer an image-reading tool if the model can
        already see images itself);
        ``route``: where this tool runs. None = handled by this process; :data:`ROUTE_EXECUTOR` =
        sent to a remote executor (dispatch delivers via ``ToolRouter.dispatch_to_executor``,
        this process needs no handler); the product layer can also use its own string to mark a
        different channel (e.g. a resource node), recognized by the product layer's own executor.
        **Mark the route when defining the tool, and routing then belongs to the framework** --
        no need to separately maintain a list of "which names belong to the executor".
        """
        if domain_path not in self.tools:
            self.tools[domain_path] = {}
        tool_def_with_handler = tool_def.copy()
        if handler:
            tool_def_with_handler["_handler"] = handler
        if requires is not None:
            tool_def_with_handler["_requires"] = list(requires)
        if hidden_when is not None:
            tool_def_with_handler["_hidden_when"] = list(hidden_when)
        if route is not None:
            tool_def_with_handler["_route"] = str(route)
        self.tools[domain_path][tool_name] = tool_def_with_handler

    def annotate_tool(
        self, domain_path: str, tool_name: str, *,
        requires: Optional[List[str]] = None, hidden_when: Optional[List[str]] = None,
        route: Optional[str] = None,
    ) -> None:
        """Add capability tags / route to an already-registered tool (used by the product layer to batch-tag tools from a table)."""
        entry = self.tools.get(domain_path, {}).get(tool_name)
        if entry is None:
            raise KeyError(f"annotate_tool: tool not registered: {domain_path}/{tool_name}")
        if requires is not None:
            entry["_requires"] = list(requires)
        if hidden_when is not None:
            entry["_hidden_when"] = list(hidden_when)
        if route is not None:
            entry["_route"] = str(route)

    # -- Route lookup ---------------------------------------------------------
    def route_of(self, function_name: str) -> Optional[str]:
        """The route marked at tool registration (None = handled by this process). If multiple packages register the same name, the first one registered wins."""
        for defs in self.tools.values():
            entry = defs.get(function_name)
            if entry is not None:
                route = entry.get("_route")
                return str(route) if route else None
        return None

    def is_executor_routed(self, function_name: str) -> bool:
        """Whether this tool is registered to be sent to a remote executor."""
        return self.route_of(function_name) == ROUTE_EXECUTOR

    def domain_of(self, function_name: str) -> Optional[str]:
        """The package path the tool lives in (first registered wins), or None if unregistered. Used by the "open package before use" gate."""
        for path, defs in self.tools.items():
            if function_name in defs:
                return path
        return None

    def register_unified_handler(
        self, domain_path: str, tool_name: str, adapter: Callable[[Dict[str, Any], "ToolContext"], Any],
    ) -> None:
        """Attach a unified ``(arguments, ctx)`` signature adapter (``_uhandler``) to an already-registered tool.
        The dispatcher prefers ``_uhandler`` when present; tools without one are wrapped on the fly with ``kwargs_adapter``."""
        entry = self.tools.get(domain_path, {}).get(tool_name)
        if entry is None:
            raise KeyError(f"register_unified_handler: tool not registered: {domain_path}/{tool_name}")
        entry["_uhandler"] = adapter

    # -- Queries --------------------------------------------------------------
    def tools_for_domain(self, domain_path: str) -> List[Dict[str, Any]]:
        """Tools under ``domain_path`` (litellm format) -- **exact match on that path, does not collect sub-packages**.
        A sub-package's tools only become available once that sub-package's path is explicitly opened; opening the parent package only exposes the sub-package summary."""
        tools: List[Dict[str, Any]] = []
        defs = self.tools.get(domain_path)
        if defs is not None:
            for _tool_name, tool_def in defs.items():
                tools.append({k: v for k, v in tool_def.items() if not k.startswith("_")})
        return tools

    def tool_handler(self, domain_path: str, tool_name: str) -> Optional[Callable[..., Any]]:
        entry = self.tools.get(domain_path, {}).get(tool_name)
        return None if entry is None else entry.get("_handler")

    def unified_handler(self, domain_path: str, tool_name: str) -> Optional[Callable[..., Any]]:
        entry = self.tools.get(domain_path, {}).get(tool_name)
        return None if entry is None else entry.get("_uhandler")

    def has_package(self, path: str) -> bool:
        """Whether the path has tools (an openable package)."""
        return bool(self.tools.get(path))

    def available_domains(self) -> List[Dict[str, Any]]:
        """The list of openable packages (excluding the root ``/tools``): packages with tools, plus "group packages" that act as parents.

        A group package carries no tools of its own; its value lies in a summary line plus the
        sub-package index it exposes once opened. Without this clause, tool-less packages would
        be filtered out entirely by the "has tools" criterion -- the parent wouldn't make it
        into the catalog, so sub-packages couldn't find their parent, and there would be no way
        to have a collapsible hierarchy (catalog rendering determines parent/child by path
        prefix, and the parent must be present in the catalog)."""
        group_paths = {
            p.rsplit("/", 1)[0]
            for p in self.packages
            if p.count("/") > 1 and p.rsplit("/", 1)[0] in self.packages
        }
        domains: List[Dict[str, Any]] = []
        for path, info in self.packages.items():
            if path == "/tools":
                continue
            if bool(self.tools.get(path)) or path in group_paths:
                domains.append({"path": path, "name": info["name"], "description": info["description"]})
        return domains

    def catalog_json(self, *, include_root: bool = True, include_parameters: bool = False) -> Dict[str, Any]:
        """The "tool package catalog" JSON: every package with tools, along with its tools' names/descriptions (parameters optional)."""
        packages: List[Dict[str, Any]] = []
        for path in sorted(self.tools.keys()):
            if not include_root and path == "/tools":
                continue
            info = self.packages.get(path, {"name": path, "description": ""})
            tools: List[Dict[str, Any]] = []
            for _tool_name, tool_def in self.tools.get(path, {}).items():
                fn = cast(Dict[str, Any], tool_def.get("function") or {})
                tool_item: Dict[str, Any] = {
                    "name": fn.get("name") or _tool_name,
                    "description": fn.get("description") or "",
                }
                if include_parameters:
                    tool_item["parameters"] = fn.get("parameters") or {}
                tools.append(tool_item)
            if not tools:
                continue     # Don't expose empty packages
            packages.append({
                "path": path,
                "name": info.get("name") or path,
                "description": info.get("description") or "",
                "tools": sorted(tools, key=lambda x: x.get("name", "")),
            })
        return {"root": "/tools", "packages": packages}

    def visible_tools(self, domains: List[str], capabilities: Set[str]) -> List[Dict[str, Any]]:
        """The tools visible to the model this step (litellm format, ordered by domain then registration order, deduplicated by name).

        Rule: walk ``domains`` (root + opened packages) domain by domain to collect tools;
        if package-level ``requires`` is missing any tag -> skip the whole package;
        if tool-level ``requires`` is missing any tag -> skip; if ``hidden_when`` contains any
        tag -> skip (redundant for the current product layer).
        ``capabilities`` is the product layer's current set of capability tags
        (``Runner.available_capabilities``).
        """
        out: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for domain in domains:
            if any(tag not in capabilities for tag in self.package_requires.get(domain, [])):
                continue
            for tool_name, tool_def in self.tools.get(domain, {}).items():
                fn = cast(Dict[str, Any], tool_def.get("function") or {})
                name = str(fn.get("name") or tool_name)
                if name in seen:
                    continue
                if any(tag not in capabilities for tag in cast(List[str], tool_def.get("_requires") or [])):
                    continue
                if any(tag in capabilities for tag in cast(List[str], tool_def.get("_hidden_when") or [])):
                    continue
                seen.add(name)
                out.append({k: v for k, v in tool_def.items() if not k.startswith("_")})
        return out

    def opened_package_prompts(self, opened_packages: List[str]) -> str:
        """Concatenated system-prompt snippets for each opened package (only packages with a non-empty prompt are included)."""
        parts: List[str] = []
        for path in opened_packages or []:
            if not path or path == "/tools":
                continue
            prompt = self.package_prompts.get(path, "").strip()
            if prompt:
                parts.append(prompt)
        return "\n\n".join(parts) if parts else ""


#: Module-level default registry. Used by ``Runtime(registry=None)`` and the module-level functions below.
DEFAULT_REGISTRY = ToolRegistry()

# Compatibility names: these three tables are exactly the three dicts inside the default
# registry (the same objects, so a product layer's ``.update()`` takes effect immediately).
TOOL_REGISTRY: Dict[str, Dict[str, Dict[str, Any]]] = DEFAULT_REGISTRY.tools
DOMAIN_INFO: Dict[str, Dict[str, Any]] = DEFAULT_REGISTRY.packages
PACKAGE_SYSTEM_PROMPTS: Dict[str, str] = DEFAULT_REGISTRY.package_prompts


# -- Module-level functions: all delegate to the default registry (saves effort for product layers that only need a single instance) --

def register_package(path: str, *, name: str, description: str, system_prompt: Optional[str] = None) -> None:
    DEFAULT_REGISTRY.register_package(path, name=name, description=description, system_prompt=system_prompt)


def register_tool(
    domain_path: str, tool_name: str, tool_def: Dict[str, Any],
    handler: Optional[Callable[..., Any]] = None, *,
    route: Optional[str] = None,
) -> None:
    DEFAULT_REGISTRY.register_tool(domain_path, tool_name, tool_def, handler, route=route)


def get_tools_for_domain(domain_path: str) -> List[Dict[str, Any]]:
    return DEFAULT_REGISTRY.tools_for_domain(domain_path)


def get_tool_handler(domain_path: str, tool_name: str) -> Optional[Callable[..., Any]]:
    return DEFAULT_REGISTRY.tool_handler(domain_path, tool_name)


def register_unified_handler(
    domain_path: str, tool_name: str, adapter: Callable[[Dict[str, Any], "ToolContext"], Any],
) -> None:
    DEFAULT_REGISTRY.register_unified_handler(domain_path, tool_name, adapter)


def get_unified_handler(domain_path: str, tool_name: str) -> Optional[Callable[..., Any]]:
    return DEFAULT_REGISTRY.unified_handler(domain_path, tool_name)


def get_available_domains() -> List[Dict[str, Any]]:
    return DEFAULT_REGISTRY.available_domains()


def get_tool_catalog_json(*, include_root: bool = True, include_parameters: bool = False) -> Dict[str, Any]:
    return DEFAULT_REGISTRY.catalog_json(include_root=include_root, include_parameters=include_parameters)


def get_opened_package_prompts(opened_packages: List[str]) -> str:
    return DEFAULT_REGISTRY.opened_package_prompts(opened_packages)
