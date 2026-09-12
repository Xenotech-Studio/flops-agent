"""Tool package session state belongs to the framework: opening/closing packages mutates Session,
persistence goes through patch_meta; navigation tools ship with the framework; opened packages can
be replayed from history after truncation."""
import asyncio
import json
import os
import sys
from types import SimpleNamespace as SN

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent import (  # noqa: E402
    Contributor, InMemoryDatabase, Query, Runtime, Session, ToolRegistry, register_navigation_tools,
)
from flops_agent.entities import events as ev  # noqa: E402


def _tool(name):
    return {"type": "function", "function": {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}}}


def _chunk(content=None, calls=None):
    d = {"content": content, "reasoning_content": None, "tool_calls": None}
    if calls:
        d["tool_calls"] = [SN(index=i, id=f"c{i}", type="function", function=SN(name=n, arguments=json.dumps(a))) for i, (n, a) in enumerate(calls)]
    return SN(choices=[SN(delta=SN(**d), finish_reason=None, index=0)], usage=None)


class ScriptedLLM:
    def __init__(self, *steps):
        self.steps, self.i = list(steps), 0

    async def acompletion(self, **req):
        step = self.steps[self.i]; self.i += 1
        async def gen():
            for c in step:
                yield c
        return gen()


def _registry():
    reg = ToolRegistry()
    register_navigation_tools(reg)
    reg.register_package("/tools/weather", name="Weather", description="Check the weather")
    async def get_weather(city: str = "Shanghai"):
        return {"city": city, "temp": 26}
    reg.register_tool("/tools/weather", "get_weather", _tool("get_weather"), get_weather)
    reg.register_package("/tools/empty", name="Empty", description="No tools")
    return reg


def test_open_and_close_change_session_and_persist():
    async def go():
        db = InMemoryDatabase()
        rt = Runtime(registry=_registry(), database=db)
        s = await rt.load_session("s1", owner_id="u1")
        assert s.opened_packages == []
        r = rt.open_packages(s, ["/tools/weather"])
        assert r["success"] and r["opened_tool_packages"] == ["/tools/weather"] and "Opened 1 tool package" in r["message"]
        assert s.opened_packages == ["/tools/weather"]
        assert (db.load_meta("s1", owner_id="u1") or {}).get("opened_tool_packages") == ["/tools/weather"], "persisted via patch_meta"
        assert rt.open_packages(s, ["/tools/nope"])["error"].startswith("Tool package does not exist or is empty")
        assert rt.open_packages(s, ["/tools/empty"])["error"].startswith("Tool package does not exist or is empty"), "a package with no tools cannot be opened"
        assert rt.open_packages(s, ["bad"])["error"].startswith("Invalid tool package path")
        assert rt.open_packages(s, "x")["error"] == "Invalid tool package path: x (must start with /tools/)"
        assert rt.open_packages(s, ["/tools"])["error"] == "No valid tool package paths to open were provided"
        r = rt.close_packages(s, ["/tools/weather", "/tools/whatever"])
        assert r["success"] and s.opened_packages == [] and "Closed 2 tool package" in r["message"]
    asyncio.run(go())
    print("test_open_and_close_change_session_and_persist OK")


def test_navigation_tools_run_inside_the_kernel_loop():
    """The model opens a package first and then calls a tool inside it: both steps go through the
    framework's default executor and registry, no host code needed."""
    async def go():
        llm = ScriptedLLM(
            [_chunk(calls=[("open_tool_packages", {"package_paths": ["/tools/weather"]})])],
            [_chunk(calls=[("get_weather", {"city": "Beijing"})])],
            [_chunk(content="It's 26 degrees in Beijing.")],
        )
        rt = Runtime(llm=llm, registry=_registry(), database=InMemoryDatabase())
        s = await rt.load_session("s1", owner_id="u1")
        run = rt.start(s, Query(content="weather in Beijing", by=Contributor.USER))
        events = [d.event async for d in run.subscribe()]
        results = [e for e in events if isinstance(e, ev.ToolResult)]
        assert len(results) == 2, [type(e).__name__ for e in events]
        assert s.opened_packages == ["/tools/weather"]
        tool_msgs = [m for m in s.messages if m.get("role") == "tool"]
        assert "Opened 1 tool package" in json.dumps(tool_msgs[0].get("content"), ensure_ascii=False)
        assert "Beijing" in json.dumps(tool_msgs[1].get("content"), ensure_ascii=False)
        assert run.status.name == "DONE"
    asyncio.run(go())
    print("test_navigation_tools_run_inside_the_kernel_loop OK")


def test_tool_in_unopened_package_is_gated():
    async def go():
        llm = ScriptedLLM([_chunk(calls=[("get_weather", {})])], [_chunk(content="OK.")])
        rt = Runtime(llm=llm, registry=_registry(), database=InMemoryDatabase())
        s = await rt.load_session("s1", owner_id="u1")
        run = rt.start(s, Query(content="weather", by=Contributor.USER))
        async for _ in run.subscribe():
            pass
        tool_msg = next(m for m in s.messages if m.get("role") == "tool")
        body = json.dumps(tool_msg.get("content"), ensure_ascii=False)
        assert "open_tool_packages" in body and "/tools/weather" in body
    asyncio.run(go())
    print("test_tool_in_unopened_package_is_gated OK")


def test_replay_opened_packages_from_history():
    hist = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "tool_calls": [{"function": {"name": "open_tool_packages", "arguments": json.dumps({"package_paths": ["/tools/a", "/tools/b"]})}}]},
        {"role": "assistant", "tool_calls": [{"function": {"name": "close_tool_packages", "arguments": {"package_paths": "/tools/a"}}}]},
        {"role": "assistant", "tool_calls": [{"function": {"name": "open_tool_packages", "arguments": "not json"}}]},
    ]
    assert Session.opened_packages_from_history(hist) == ["/tools/b"]
    print("test_replay_opened_packages_from_history OK")


def test_visible_tools_follow_opened_packages_and_capability_tags():
    """Visible = root + opened packages (including overlay packages) - packages/tools missing a
    capability - extras the host doesn't need; duplicate names are deduped, order is stable."""
    reg = ToolRegistry()
    register_navigation_tools(reg)
    reg.register_tool("/tools", "read_image", _tool("read_image"), hidden_when=["model:vision"])
    reg.register_package("/tools/a", name="A", description="")
    reg.register_tool("/tools/a", "a1", _tool("a1"))
    reg.register_tool("/tools/a", "a2", _tool("a2"), requires=["cap.x"])
    reg.register_package("/tools/remote", name="R", description="", requires=["executor:online"])
    reg.register_tool("/tools/remote", "r1", _tool("r1"))
    reg.register_package("/tools/ov", name="O", description="")
    reg.register_tool("/tools/ov", "o1", _tool("o1"))
    reg.register_tool("/tools/ov", "a1", _tool("a1"))          # same name as /tools/a -> deduped

    names = lambda tools: [t["function"]["name"] for t in tools]
    root = ["open_tool_packages", "close_tool_packages", "read_image"]
    assert names(reg.visible_tools(["/tools"], set())) == root
    assert names(reg.visible_tools(["/tools", "/tools/a"], set())) == root + ["a1"], "a2 is missing cap.x"
    assert names(reg.visible_tools(["/tools", "/tools/a"], {"cap.x"})) == root + ["a1", "a2"]
    assert names(reg.visible_tools(["/tools", "/tools/remote"], set())) == root, "the whole package requires the executor to be online"
    assert names(reg.visible_tools(["/tools", "/tools/remote"], {"executor:online"})) == root + ["r1"]
    assert names(reg.visible_tools(["/tools"], {"model:vision"})) == root[:2], "vision models don't get the read-image tool"
    assert names(reg.visible_tools(["/tools", "/tools/a", "/tools/ov"], set())) == root + ["a1", "o1"], "duplicate names dedupe by first occurrence"

    s = Session("s1", meta={"opened_tool_packages": ["/tools/a"], "overlay_tool_packages": ["/tools/ov", "/tools/a"]})
    assert s.effective_packages == ["/tools/a", "/tools/ov"]
    print("test_visible_tools_follow_opened_packages_and_capability_tags OK")


def test_runner_hooks_drive_visibility_into_the_request():
    """The tools in build_request come from Runner.visible_tools; available_capabilities is where
    the host attaches capability tags."""
    async def go():
        from flops_agent import Runner
        reg = ToolRegistry()
        reg.register_package("/tools/remote", name="R", description="", requires=["executor:online"])
        reg.register_tool("/tools/remote", "r1", _tool("r1"))

        class Offline(Runner):
            pass

        class Online(Runner):
            def available_capabilities(self):
                return {"executor:online"}

        seen = {}
        class Capture:
            async def acompletion(self, **req):
                seen["tools"] = [t["function"]["name"] for t in req.get("tools") or []]
                async def gen():
                    yield _chunk(content="ok")
                return gen()
        for cls in (Offline, Online):
            rt = Runtime(llm=Capture(), registry=reg, database=InMemoryDatabase(), runner=cls)
            s = await rt.load_session("s", owner_id="u")
            rt.open_packages(s, ["/tools/remote"])
            run = rt.start(s, Query(content="hi", by=Contributor.USER))
            async for _ in run.subscribe():
                pass
            seen[cls.__name__] = seen.pop("tools")
        assert seen["Offline"] == [] and seen["Online"] == ["r1"], seen
    asyncio.run(go())
    print("test_runner_hooks_drive_visibility_into_the_request OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in _TESTS:
        t()
    print(f"\nALL {len(_TESTS)} PACKAGE TESTS PASSED")
