"""Regression tests for the sample product -- it must keep working, always.

The whole value of a sample file is that "copy it and it works." Once the
framework changes a signature and the sample doesn't keep up, it turns from a
tutorial into a trap: the reader copies it, hits the same TypeError in their
own code, and assumes they made the mistake.

So this doesn't test whether the output looks nice -- only that **it still
holds against the current framework**.
"""
import asyncio
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

def load_sample():
    # The sample is now a package (server / local_executor / frontend layers); import the server layer as a package.
    import importlib
    return importlib.import_module("flops_agent.docs.sample_product.server")


sample = load_sample()


def _sse_type(line: str) -> str:
    """Extract the event type from an SSE line emitted by handle_chat_request (the sample server's wire convention)."""
    import json
    return json.loads(line[len("data: "):]).get("type", "?")


def collect(script, text):
    """Run one turn and collect all event type names (parsing the server layer's
    SSE wire -- the endpoint emits bytes, not Delivery objects, which is exactly
    the shape a real product has)."""
    async def _run():
        runtime = sample.build_runtime(sample.ScriptedLLM(script))
        return [_sse_type(line) async for line in sample.handle_chat_request(runtime, "t", text)]
    return asyncio.run(_run())


def test_sample_imports_against_the_current_kernel():
    """The sample only imports names that exist in the contract -- if the framework removes a name, this goes red first."""
    assert callable(sample.build_runtime) and callable(sample.handle_chat_request)
    print("test_sample_imports_against_the_current_kernel OK")


def test_plain_text_turn_streams_and_ends():
    events = collect([{"chunks": ["hello, ", "world"]}], "hi")
    assert events.count("text_delta") == 2
    assert events[-1] == "loop_finished"
    assert "error" not in events
    print("test_plain_text_turn_streams_and_ends OK")


def test_safe_tool_call_actually_executes():
    events = collect(
        [{"tool_calls": [{"id": "c1", "name": "run_command",
                          "arguments": {"command": "ls -la"}}]},
         {"content": "done"}],
        "check the directory",
    )
    assert "tool_executing" in events, "a command not on the danger rule table should execute as normal"
    assert "tool_result" in events and "error" not in events
    print("test_safe_tool_call_actually_executes OK")


def test_dangerous_command_is_blocked_before_execution():
    """Core demonstration: the gate blocks **before** execution, not after the fact.

    Once ToolExecuting appears, the command is already running on the user's
    machine -- rejecting it at that point is pointless.
    """
    events = collect(
        [{"tool_calls": [{"id": "c2", "name": "run_command",
                          "arguments": {"command": "rm -rf /tmp/x"}}]}],
        "delete it",
    )
    assert "tool_executing" not in events, "a dangerous command must not reach execution"
    assert "tool_result" in events, "but the rejection must be handed back to the model as a tool result, so it can change approach"
    print("test_dangerous_command_is_blocked_before_execution OK")


def test_run_failure_reaches_subscribers_as_an_error_event():
    """Framework behavior added this round: subscribers must see it when a run blows up.

    Previously finish() only recorded the error on the Run object, and
    subscribers received a plain end-of-stream sentinel -- there was no way to
    tell "finished normally" from "blew up," so the user saw a reply that just
    stops with no indication anything went wrong.
    """
    class Exploding:
        async def acompletion(self, **kw):
            raise RuntimeError("provider crashed")

    async def _run():
        runtime = sample.build_runtime(sample.ScriptedLLM([]))
        runtime.llm = Exploding()
        session = runtime.load_session_sync("t")
        run = runtime.start(session, sample.Query(content="hi", by=sample.Contributor.USER))
        return [type(getattr(d, "event", d)).__name__ async for d in run.subscribe()]

    events = asyncio.run(_run())
    assert "Error" in events, "a failure must reach subscribers as an Error event"
    print("test_run_failure_reaches_subscribers_as_an_error_event OK")


def test_error_event_carries_the_reason():
    class Exploding:
        async def acompletion(self, **kw):
            raise RuntimeError("provider crashed")

    async def _run():
        runtime = sample.build_runtime(sample.ScriptedLLM([]))
        runtime.llm = Exploding()
        session = runtime.load_session_sync("t")
        run = runtime.start(session, sample.Query(content="hi", by=sample.Contributor.USER))
        return [getattr(d, "event", d) async for d in run.subscribe()]

    errors = [e for e in asyncio.run(_run()) if type(e).__name__ == "Error"]
    assert errors and "provider crashed" in errors[0].message
    print("test_error_event_carries_the_reason OK")


def test_session_round_trips_through_the_fine_grained_database():
    """Another gap filled this round: Runtime assembles the session using primitives that actually exist in the protocol.

    Previously it called database.load_session / save_session -- the Database
    protocol never declares either one, the framework's own InMemoryDatabase
    doesn't have them either, and anyone implementing a backend against the
    protocol would hit an AttributeError the moment they ran it.
    """
    async def _run():
        runtime = sample.build_runtime(sample.ScriptedLLM([{"chunks": ["got it"]}]))
        session = runtime.load_session_sync("persist-me")
        run = runtime.start(session, sample.Query(content="hi", by=sample.Contributor.USER))
        async for _ in run.subscribe():
            pass
        return runtime.load_session_sync("persist-me")

    reloaded = asyncio.run(_run())
    assert reloaded.messages, "a conversation that was written must be readable back out"
    print("test_session_round_trips_through_the_fine_grained_database OK")


def test_agent_identity_reaches_the_llm_request():
    """The seam is live: persona and memory actually make it into the request, not just consistent within the entity itself."""
    seen = {}

    class Recording:
        async def acompletion(self, **request):
            seen.update(request)
            return sample._ScriptedStream({"chunks": ["ok"]})

    async def _go():
        mem = sample.NotebookMemory()
        mem.notes.append("the user's name is Xiao Ming")
        agent = sample.Agent(instructions="You speak very little", memory=mem)
        runtime = sample.build_runtime(sample.ScriptedLLM([]), agent=agent)
        runtime.llm = Recording()
        session = runtime.load_session_sync("t")
        run = runtime.start(session, sample.Query(content="hi", by=sample.Contributor.USER))
        async for _ in run.subscribe():
            pass

    asyncio.run(_go())
    system = [m for m in seen["messages"] if m.get("role") == "system"]
    assert len(system) == 1, "there should be exactly one system message"
    assert "You speak very little" in system[0]["content"] and "the user's name is Xiao Ming" in system[0]["content"]
    # Tools don't belong to the Agent: the visible set comes from the runtime registry (the root package is always visible) plus what the session has unpacked.
    assert [t["function"]["name"] for t in seen["tools"]] == ["run_command"]
    print("test_agent_identity_reaches_the_llm_request OK")


def test_memory_update_does_not_block_the_subscriber():
    """Memory hasn't finished writing when the subscriber finishes draining the stream -- that's exactly the point of not awaiting it."""
    class SlowMemory(sample.NotebookMemory):
        async def remember(self, session):
            await asyncio.sleep(0.2)
            await super().remember(session)

    async def _go():
        mem = SlowMemory()
        agent = sample.Agent(instructions="p", memory=mem)
        runtime = sample.build_runtime(sample.ScriptedLLM([{"chunks": ["ok"]}]), agent=agent)
        session = runtime.load_session_sync("t")
        run = runtime.start(session, sample.Query(content="hi", by=sample.Contributor.USER))
        async for _ in run.subscribe():
            pass
        return mem.notes                      # the exact moment the stream ends

    assert asyncio.run(_go()) == [], "if memory has already finished writing, the subscriber was blocked waiting on the memory update"
    print("test_memory_update_does_not_block_the_subscriber OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in _TESTS:
        t()
    print(f"\nALL {len(_TESTS)} SAMPLE-PRODUCT TESTS PASSED")
