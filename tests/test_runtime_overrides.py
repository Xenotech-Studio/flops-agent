"""Runtime derivation must preserve every configuration slot not overridden."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent import (  # noqa: E402
    InMemoryDatabase,
    InMemoryRunStore,
    LLMStreamRetryPolicy,
    MemoryInbox,
    Run,
    Runner,
    Runtime,
    Session,
    ToolRegistry,
    WireCodec,
)


def test_with_overrides_preserves_the_runtime_configuration_matrix():
    """Changing one setting must not drop an unrelated injection or policy."""
    llm = object()
    tools = []
    database = InMemoryDatabase()
    executor = object()
    agent = object()
    coalescer = object()
    run_store = InMemoryRunStore()
    inbox = MemoryInbox()
    wire = WireCodec()
    retry = LLMStreamRetryPolicy(max_attempts=7)
    registry = ToolRegistry()
    reply_plan = {"kind": "prefill"}
    marked = object()
    cleared = object()
    runtime = Runtime(
        llm=llm,
        tools=tools,
        database=database,
        executor=executor,
        agent=agent,
        runner=Runner,
        coalescer=coalescer,
        session_class=Session,
        run_class=Run,
        run_store=run_store,
        inbox=inbox,
        wire=wire,
        model="base-model",
        rescue_silent_reply=False,
        reply_rescue_plan=reply_plan,
        silent_reply_max_rescues=3,
        llm_stream_retry=retry,
        registry=registry,
        temperature=0.1,
        top_p=0.8,
    )
    runtime.on_session_run_marked = marked
    runtime.on_session_run_cleared = cleared
    runtime.stop_poll_interval = 0.125

    derived = runtime.with_overrides(model="derived-model", temperature=0.2)

    expected = {
        "llm": llm,
        "tools": tools,
        "database": database,
        "executor": executor,
        "agent": agent,
        "runner": Runner,
        "coalescer": coalescer,
        "session_class": Session,
        "run_class": Run,
        "run_store": run_store,
        "inbox": inbox,
        "wire": wire,
        "rescue_silent_reply": False,
        "reply_rescue_plan": reply_plan,
        "silent_reply_max_rescues": 3,
        "llm_stream_retry": retry,
        "registry": registry,
        "on_session_run_marked": marked,
        "on_session_run_cleared": cleared,
        "stop_poll_interval": 0.125,
    }
    for name, value in expected.items():
        assert getattr(derived, name) is value, name
    assert derived.model == "derived-model" and runtime.model == "base-model"
    assert derived.completion_kwargs == {"temperature": 0.2, "top_p": 0.8}
    assert runtime.completion_kwargs == {"temperature": 0.1, "top_p": 0.8}
    assert derived.runs is runtime.runs


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for test in _TESTS:
        test()
    print(f"\\nALL {len(_TESTS)} RUNTIME OVERRIDE TESTS PASSED")
