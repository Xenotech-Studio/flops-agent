"""The tool registry is an object owned by Runtime: two Runtimes in the same process each get their
own; module-level functions still write to the default instance."""
import asyncio
import os
import sys
from types import SimpleNamespace as SN

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent import DEFAULT_REGISTRY, ToolContext, ToolRegistry  # noqa: E402
from flops_agent.tools import registry as reg_mod  # noqa: E402
from flops_agent.tools.dispatch import dispatch_tool  # noqa: E402


def _tool(name):
    return {"type": "function", "function": {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}}}


def _call(name):
    return SN(function=SN(name=name, arguments="{}"))


def test_two_registries_do_not_see_each_other():
    a, b = ToolRegistry(), ToolRegistry()
    a.register_package("/tools/x", name="X", description="x pkg")
    a.register_tool("/tools/x", "ping", _tool("ping"), lambda: {"ok": "a"})
    assert a.has_package("/tools/x") and not b.has_package("/tools/x")
    assert [d["path"] for d in a.available_domains()] == ["/tools/x"]
    assert b.available_domains() == []
    print("test_two_registries_do_not_see_each_other OK")


def test_dispatch_resolves_in_the_context_registry():
    async def go():
        a, b = ToolRegistry(), ToolRegistry()
        async def ping_a(): return {"from": "a"}
        async def ping_b(): return {"from": "b"}
        a.register_tool("/tools/x", "ping", _tool("ping"), ping_a)
        b.register_tool("/tools/x", "ping", _tool("ping"), ping_b)
        ctx = lambda r: ToolContext(user_id="u", conversation_id="c", function_name="ping", tool_domains=["/tools/x"], registry=r)
        assert (await dispatch_tool(_call("ping"), {}, ctx(a))) == {"from": "a"}
        assert (await dispatch_tool(_call("ping"), {}, ctx(b))) == {"from": "b"}
        # package not opened -> rejected, reporting "which package to open" from the registry it belongs to
        a.register_package("/tools/x", name="X Package", description="")
        closed = await dispatch_tool(_call("ping"), {}, ToolContext(user_id="u", conversation_id="c", function_name="ping", tool_domains=["/tools"], registry=a))
        assert closed["success"] is False and closed.get("required_package_path") == "/tools/x" and "X Package" in closed["hint"]
    asyncio.run(go())
    print("test_dispatch_resolves_in_the_context_registry OK")


def test_module_level_api_targets_the_default_registry():
    reg_mod.register_package("/tools/legacy", name="L", description="legacy")
    reg_mod.register_tool("/tools/legacy", "old", _tool("old"), lambda: {})
    try:
        assert DEFAULT_REGISTRY.has_package("/tools/legacy")
        assert reg_mod.TOOL_REGISTRY is DEFAULT_REGISTRY.tools and reg_mod.DOMAIN_INFO is DEFAULT_REGISTRY.packages
        assert any(p["path"] == "/tools/legacy" for p in reg_mod.get_tool_catalog_json()["packages"])
        assert reg_mod.get_opened_package_prompts(["/tools/legacy"]) == ""
        reg_mod.PACKAGE_SYSTEM_PROMPTS["/tools/legacy"] = "prompt"
        assert DEFAULT_REGISTRY.opened_package_prompts(["/tools/legacy"]) == "prompt"
    finally:
        DEFAULT_REGISTRY.tools.pop("/tools/legacy", None); DEFAULT_REGISTRY.packages.pop("/tools/legacy", None); DEFAULT_REGISTRY.package_prompts.pop("/tools/legacy", None)
    print("test_module_level_api_targets_the_default_registry OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in _TESTS:
        t()
    print(f"\nALL {len(_TESTS)} REGISTRY TESTS PASSED")
