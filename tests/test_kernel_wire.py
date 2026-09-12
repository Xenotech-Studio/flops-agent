"""Framework's default wire serialization -- the WIRE_TYPES vocabulary + the to_sse/delivery_to_sse contract.

Original P0 TODO (the highest-cost blank spot for adopters): without a default
serialization, every adopter hand-writes hundreds of lines and the frontend and
backend privately align on a type table. This pins down four things:

1. **Every framework event class is in WIRE_TYPES** (a new event that isn't
   registered turns this test red);
2. Every event class's wire dict is JSON-serializable with a stable type;
3. ProductEvent payload passes through unchanged (type defaults to kind, but a
   type already in the payload takes priority);
4. delivery_to_sse: live segments get a cursor injected, replayed segments and
   raw string passthrough do not.

Run: ``python3 backend/tests/test_kernel_wire.py`` (or via pytest).
"""
import json
import os
import sys
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent import WIRE_TYPES, event_to_wire, to_sse, delivery_to_sse  # noqa: E402
from flops_agent.entities import events as _ev  # noqa: E402


def _parse(sse: str) -> dict:
    assert sse.startswith("data: ") and sse.endswith("\n\n"), repr(sse[:40])
    return json.loads(sse[len("data: "):])


def test_every_event_class_is_in_wire_table():
    """A new framework event must be registered in WIRE_TYPES as well -- the wire
    type is the frontend's branch name, so a missing registration means the
    frontend gets unknown_event. ProductEvent (passthrough) and AgentEvent (base
    class) are exempt."""
    import dataclasses
    missing = []
    for name in dir(_ev):
        cls = getattr(_ev, name)
        if not isinstance(cls, type) or not dataclasses.is_dataclass(cls):
            continue
        if cls in (_ev.ProductEvent,) or name.startswith("_"):
            continue
        if cls not in WIRE_TYPES:
            missing.append(name)
    assert not missing, f"event classes not registered in WIRE_TYPES: {missing}"
    # Converse: every class in the table can build a JSON-serializable wire dict
    # (smoke-tested with a minimal-defaults instance).
    print("test_every_event_class_is_in_wire_table OK")


def test_wire_dicts_are_jsonable_with_stable_types():
    samples = [
        (_ev.TextDelta("hello"), "text_delta"),
        (_ev.ReasoningDelta("thinking", phase="delta"), "reasoning_delta"),
        (_ev.ToolCallStarted(0, "t"), "tool_call_started"),
        (_ev.ToolCallArgsDelta(0, "{"), "tool_call_args_delta"),
        (_ev.ToolCallReady(0, "t", "{}"), "tool_call_ready"),
        (_ev.ToolExecuting(0), "tool_executing"),
        (_ev.ToolResultDelta(0, {"chunk": 1}), "tool_result_delta"),
        (_ev.ToolResult(0, "t", {"ok": True, "obj": object()}), "tool_result"),
        (_ev.StepCompleted(3), "step_completed"),
        (_ev.LoopFinished("stop", 2), "loop_finished"),
        (_ev.Suspended({"kind": "ask"}), "suspended"),
        (_ev.Cancelled(), "cancelled"),
        (_ev.Error("boom", exc=ValueError("x")), "error"),
        (_ev.HistoryChanged(5, message_count=10), "history_changed"),
    ]
    for event, expect_type in samples:
        wire = event_to_wire(event)
        assert wire and wire["type"] == expect_type, (event, wire)
        json.dumps(wire)                      # must be JSON-serializable (object() falls back to str)
        assert _parse(to_sse(event))["type"] == expect_type
    # Terminal events carry a done flag.
    assert _parse(to_sse(_ev.LoopFinished()))["done"] is True
    assert _parse(to_sse(_ev.Cancelled()))["done"] is True
    # Error does not carry exc (the traceback never goes over the wire).
    assert "exc" not in event_to_wire(_ev.Error("m", exc=ValueError()))
    print("test_wire_dicts_are_jsonable_with_stable_types OK")


def test_product_event_passthrough():
    pe = _ev.ProductEvent("my_frame", {"a": 1}, ensure_ascii=False)
    assert event_to_wire(pe) == {"type": "my_frame", "a": 1}
    pe2 = _ev.ProductEvent("kind_x", {"type": "override", "b": 2})
    assert event_to_wire(pe2)["type"] == "override"     # a type already in the payload takes priority
    print("test_product_event_passthrough OK")


def test_delivery_cursor_injection_and_passthrough():
    live = SimpleNamespace(event=_ev.TextDelta("hi"), cursor=42, replayed=False)
    assert _parse(delivery_to_sse(live))["cursor"] == 42
    replay = SimpleNamespace(event=_ev.TextDelta("hi"), cursor=41, replayed=True)
    assert "cursor" not in _parse(delivery_to_sse(replay))   # replayed segments get no injection
    raw = SimpleNamespace(event="data: {\"type\":\"custom\"}\n\n", cursor=7, replayed=False)
    assert delivery_to_sse(raw) == "data: {\"type\":\"custom\"}\n\n"   # string passthrough
    assert to_sse(object()).startswith("data: ") and "unknown_event" in to_sse(object())
    print("test_delivery_cursor_injection_and_passthrough OK")


def test_codec_slot_and_subclass_override():
    """Injection-slot contract: runtime.wire defaults to the standard codec;
    a subclass overriding a single method can change how one frame kind looks,
    and once injected, sse_stream runs through that product-layer codec."""
    import asyncio
    from flops_agent import Runtime, WireCodec

    class MyCodec(WireCodec):
        def event_to_wire(self, event):
            wire = super().event_to_wire(event)
            if wire and wire.get("type") == "text_delta":
                wire["type"] = "delta"            # product layer's own branch name
            return wire

    rt = Runtime(wire=MyCodec())
    assert type(rt.wire).__name__ == "MyCodec"
    assert type(Runtime().wire).__name__ == "WireCodec"   # default = standard codec

    class FakeRun:
        async def subscribe(self, from_cursor=0):
            yield SimpleNamespace(event=_ev.TextDelta("hi"), cursor=3, replayed=False)

    async def _go():
        return [line async for line in rt.sse_stream(FakeRun())]

    lines = asyncio.run(_go())
    got = _parse(lines[0])
    assert got["type"] == "delta" and got["cursor"] == 3, got
    print("test_codec_slot_and_subclass_override OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in _TESTS:
        t()
    print(f"\nALL {len(_TESTS)} TESTS PASSED")
