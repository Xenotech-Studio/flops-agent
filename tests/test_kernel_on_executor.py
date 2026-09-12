"""Routing belongs to the framework: tools are tagged with a route at registration time, and dispatch delivers to the executor based on that tag; the framework ships its own /tools/on_executor basic tool package."""
import asyncio
import json
import os
import sys
from types import SimpleNamespace as SN

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent import ROUTE_EXECUTOR, ToolContext, ToolRegistry, register_on_executor_package  # noqa: E402
from flops_agent.tools.dispatch import dispatch_tool  # noqa: E402
from flops_agent.tools.on_executor import BASIC_PATH, BASIC_TOOL_REQUIRES, BASIC_TOOLS, PACKAGE_PATH  # noqa: E402


def _tool(name):
    return {"type": "function", "function": {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}}}


def _call(name, args=None):
    return SN(id="c1", type="function", function=SN(name=name, arguments=json.dumps(args or {})))


def _ctx(reg, name, domains=("/tools", BASIC_PATH)):
    return ToolContext(user_id="u", conversation_id="s", function_name=name, tool_domains=list(domains), registry=reg)


class _Router:
    def __init__(self):
        self.sent = []

    async def dispatch_to_executor(self, tool_call, arguments, ctx):
        self.sent.append((ctx.function_name, arguments))
        return {"ok": True, "remote": True}


def test_route_annotation_drives_dispatch():
    async def go():
        reg = ToolRegistry()
        reg.register_tool("/tools", "ping", _tool("ping"), lambda: {"ok": "local"})
        reg.register_tool("/tools/x", "remote_thing", _tool("remote_thing"), None, route=ROUTE_EXECUTOR)
        reg.register_tool("/tools/x", "later", _tool("later"), None)
        reg.annotate_tool("/tools/x", "later", route="resource_node")
        assert reg.route_of("ping") is None and reg.route_of("nope") is None
        assert reg.is_executor_routed("remote_thing") and not reg.is_executor_routed("later")
        assert reg.route_of("later") == "resource_node"
        assert reg.domain_of("remote_thing") == "/tools/x" and reg.domain_of("nope") is None
        assert "_route" not in reg.tools_for_domain("/tools/x")[0], "underscore keys must not go on the wire"
        router = _Router()
        # Tagged as executor-routed -> delivered via the router; no local handler in this process, but still not reported as an unknown tool
        out = await dispatch_tool(_call("remote_thing", {"a": 1}), {"a": 1}, _ctx(reg, "remote_thing", ("/tools", "/tools/x")), router=router)
        assert out == {"ok": True, "remote": True} and router.sent == [("remote_thing", {"a": 1})]
        # Not tagged -> local handler, bypasses the router
        out = await dispatch_tool(_call("ping"), {}, _ctx(reg, "ping", ("/tools",)), router=router)
        assert out == {"ok": "local"} and len(router.sent) == 1
        # Tagged as executor-routed but no router available -> a clear error, no silent local fallback
        out = await dispatch_tool(_call("remote_thing"), {}, _ctx(reg, "remote_thing", ("/tools", "/tools/x")), router=None)
        assert out["success"] is False and "ToolRouter" in out["error"]
        # The router's is_executor_routed is an extension point: legacy tools not tagged in the registry can still be routed there
        class Legacy(_Router):
            def is_executor_routed(self, name):
                return name.startswith("legacy_")
        legacy = Legacy()
        reg.register_tool("/tools", "legacy_x", _tool("legacy_x"), None)
        out = await dispatch_tool(_call("legacy_x"), {}, _ctx(reg, "legacy_x", ("/tools",)), router=legacy)
        assert out["remote"] is True
    asyncio.run(go())
    print("test_route_annotation_drives_dispatch OK")


def test_on_executor_package_registers_nine_routed_tools():
    reg = ToolRegistry()
    names = register_on_executor_package(reg)
    assert names == [t["function"]["name"] for t in BASIC_TOOLS] and len(names) == 9
    assert {"local_exec_command", "local_read_file", "local_edit_file", "local_task_wait"} <= set(names)
    assert PACKAGE_PATH in reg.packages and BASIC_PATH in reg.packages
    assert reg.package_requires[BASIC_PATH] == ["executor:online"]
    for n in names:
        assert reg.is_executor_routed(n) and reg.domain_of(n) == BASIC_PATH
        assert reg.tools[BASIC_PATH][n]["_requires"] == BASIC_TOOL_REQUIRES[n]
        assert reg.tool_handler(BASIC_PATH, n) is None, "executor-routed tools have no handler in this process"
    # Capability filtering: if the executor doesn't report files.write, the write tool isn't visible; if the package-level executor:online capability is missing, the whole package is invisible
    caps = {"executor:online", "executor.command.exec", "executor.files.read"}
    visible = {t["function"]["name"] for t in reg.visible_tools([BASIC_PATH], caps)}
    assert visible == {"local_exec_command", "local_read_file"}
    assert reg.visible_tools([BASIC_PATH], {"executor.files.read"}) == []
    # The product layer can append its own tools into the same package / attach a sibling sub-package
    reg.register_tool(BASIC_PATH, "local_mine", _tool("local_mine"), None, route=ROUTE_EXECUTOR)
    reg.register_package(PACKAGE_PATH + "/mine", name="mine", description="mine")
    reg.register_tool(PACKAGE_PATH + "/mine", "local_other", _tool("local_other"), None, route=ROUTE_EXECUTOR)
    assert reg.is_executor_routed("local_mine") and reg.is_executor_routed("local_other")
    assert [t["function"]["name"] for t in reg.tools_for_domain(BASIC_PATH)][-1] == "local_mine", "the framework's nine tools come first, product-layer additions come after"
    print("test_on_executor_package_registers_nine_routed_tools OK")


def test_host_copy_wins_and_defaults_fill_in():
    reg = ToolRegistry()
    reg.register_package(PACKAGE_PATH, name="Product-layer group", description="Written by the product layer", system_prompt="P")
    register_on_executor_package(reg, package_requires=())
    assert reg.packages[PACKAGE_PATH]["name"] == "Product-layer group" and reg.package_prompts[PACKAGE_PATH] == "P", "copy registered by the product layer first takes precedence"
    assert reg.packages[BASIC_PATH]["name"], "falls back to the framework's default copy when unregistered"
    assert PACKAGE_PATH not in reg.package_requires, "passing an empty package_requires means it isn't set"
    reg2 = ToolRegistry()
    register_on_executor_package(reg2, basic={"name": "B", "description": "D", "system_prompt": "S"})
    assert reg2.packages[BASIC_PATH] == {"name": "B", "description": "D"} and reg2.package_prompts[BASIC_PATH] == "S"
    print("test_host_copy_wins_and_defaults_fill_in OK")


if __name__ == "__main__":
    test_route_annotation_drives_dispatch()
    test_on_executor_package_registers_nine_routed_tools()
    test_host_copy_wins_and_defaults_fill_in()
