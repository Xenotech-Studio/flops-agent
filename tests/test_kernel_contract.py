"""Framework contract gatekeeper -- whatever ``flops_agent.__all__`` claims, the package must actually provide.

This package is a framework meant for open-sourcing: ``__all__`` is not documentation, it is
**the sole reference other people write code against**. Every name in it becomes a line in
someone's import statement, and if it's wrong their build only turns red because of that.

This round's trigger: ``StepPlan`` appeared twice in ``__all__``, and the package genuinely had
**two classes with the same name but different APIs** (hooks' ``.proceed/.skip_llm/.stop`` versus
interaction's ``.call_llm/.dispatch/.finish``); whichever was imported later silently shadowed the
other. Worse, the old one used ``__slots__``, so ``hasattr(StepPlan, "skip_llm")`` returned True --
it would have been better for the duck-typing check to fail, but it passed instead, and only at
actual call time did it blow up with ``TypeError: 'member_descriptor' object is not callable``.

So what's tested here isn't behavior, it's **the internal consistency of the contract itself**.
"""
import importlib
import inspect
import os
import sys
from typing import get_args, get_type_hints

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

import flops_agent as fa  # noqa: E402


def test_no_duplicate_names():
    """A duplicate entry can mask a same-name shadowing -- exactly how this round's bug hid."""
    dupes = {n for n in fa.__all__ if fa.__all__.count(n) > 1}
    assert not dupes, f"__all__ has duplicates: {sorted(dupes)}"
    print("test_no_duplicate_names OK")


def test_every_exported_name_exists():
    """``from flops_agent import X`` must hold for every name in __all__."""
    missing = [n for n in fa.__all__ if not hasattr(fa, n)]
    assert not missing, f"names in __all__ that cannot be resolved: {missing}"
    print("test_every_exported_name_exists OK")


def test_public_typed_contracts_are_importable_and_complete():
    """A third-party integration can import and use every typed boundary."""
    from flops_agent import (  # noqa: PLC0415
        FinishStreamChunk,
        InMemoryRunStore,
        LLMStreamClient,
        RunMeta,
        StreamChunk,
        TextStreamChunk,
        ToolCall,
        ToolCallStreamChunk,
        ToolExecutor,
        ToolFunction,
        ToolOutcome,
    )
    from flops_agent.engine.stream import StreamAccumulator  # noqa: PLC0415
    from flops_agent.seams.run_store import RunStore  # noqa: PLC0415
    from flops_agent.tools.schema import tool_call_from_dict, tool_call_to_openai  # noqa: PLC0415

    call = ToolCall(id="call-1", function=ToolFunction("weather", '{"city":"Paris"}'))
    assert tool_call_from_dict(tool_call_to_openai(call)) == call
    assert get_type_hints(tool_call_from_dict)["return"] is ToolCall
    assert get_type_hints(ToolExecutor.execute)["return"] is ToolOutcome
    assert StreamChunk in get_args(get_type_hints(LLMStreamClient.acompletion)["return"])
    assert RunMeta in get_args(get_type_hints(RunStore.get_meta)["return"])
    assert InMemoryRunStore().get_meta("missing") is None

    acc = StreamAccumulator()
    assert [type(event).__name__ for event in acc.feed(TextStreamChunk("hi"))] == ["TextDelta"]
    assert [type(event).__name__ for event in acc.feed(
        ToolCallStreamChunk(0, id="call-1", name="weather", arguments_delta="{}")
    )] == ["ToolCallStarted", "ToolCallArgsDelta"]
    finish = FinishStreamChunk(reason="stop", usage={"total_tokens": 9})
    assert finish.kind == "finish" and finish.usage == {"total_tokens": 9}
    print("test_public_typed_contracts_are_importable_and_complete OK")


KNOWN_COLLISIONS = {
    # name: (submodule that defines it, expiry condition). Empty since 2026-07-23 -- the
    # hooks.StepPlan name collision disappeared with the terminology merge (alignment audit B3).
}
"""**Known** same-name-different-source cases, each with its own expiry condition.

This is not whitelist-style tolerance -- every entry records when it should go away. Any newly
appearing collision fails the test, because at that point which one gets exported depends on the
import order in __init__.py, and a single reordered line could change the public API.
"""


def test_no_new_name_collisions_appear():
    """Same name, different source -- the package should not have two classes fighting over one export name.

    Check submodule by submodule: if an exported name is defined in multiple submodules and they
    don't point to the same object, then which one gets exported depends on import order. The
    known cases are listed in KNOWN_COLLISIONS; anything else fails.
    """
    submodules = [
        "context", "database", "events", "execution", "executor", "hooks",
        "interaction", "llm_client", "query", "runner", "runtime", "safety", "session",
    ]
    conflicts = []
    for name in fa.__all__:
        exported = getattr(fa, name)
        owners = []
        for mod_name in submodules:
            try:
                mod = importlib.import_module(f"flops_agent.{mod_name}")
            except Exception:
                continue
            candidate = getattr(mod, name, None)
            if candidate is None or not (inspect.isclass(candidate) or inspect.isfunction(candidate)):
                continue
            if getattr(candidate, "__module__", "") != f"flops_agent.{mod_name}":
                continue                       # only count "defined here", re-imported ones don't count
            owners.append(mod_name)
        if len(owners) > 1 and any(getattr(fa, name) is not
                                   getattr(importlib.import_module(f"flops_agent.{m}"), name)
                                   for m in owners):
            conflicts.append((name, owners, exported.__module__))
    unexpected = [
        c for c in conflicts
        if c[0] not in KNOWN_COLLISIONS or set(c[1]) != KNOWN_COLLISIONS[c[0]][0]
    ]
    assert not unexpected, f"newly appearing same-name-different-source case, export depends on import order: {unexpected}"
    print("test_no_new_name_collisions_appear OK")


def test_hooks_module_is_gone():
    """The two terminologies have been merged (2026-07-23 alignment audit B3): hooks.py is retired,
    ToolGate/TurnDecision folded into interaction; StepPlan/Interaction now have only the entity form."""
    try:
        import flops_agent.hooks  # noqa: F401
    except ModuleNotFoundError:
        print("test_hooks_module_is_gone OK")
        return
    raise AssertionError("flops_agent.hooks should have been retired")


def test_step_plan_is_the_entity_form():
    """The StepPlan in the contract is the entity from the whitepaper, not the transitional hooks form."""
    assert fa.StepPlan.__module__ == "flops_agent.engine.interaction"
    for verb in ("call_llm", "dispatch", "finish"):
        assert callable(getattr(fa.StepPlan, verb)), verb
    print("test_step_plan_is_the_entity_form OK")



def test_callable_looking_attributes_are_actually_callable():
    """A ``__slots__`` member descriptor can make hasattr lie (this round's exact trap).

    Check exported classes one by one: documented constructor methods must actually be
    callable, not just "appear to exist".
    """
    bad = []
    for name in fa.__all__:
        obj = getattr(fa, name)
        if not inspect.isclass(obj):
            continue
        for attr in dir(obj):
            if attr.startswith("_"):
                continue
            member = inspect.getattr_static(obj, attr, None)
            if type(member).__name__ == "member_descriptor" and callable(getattr(obj, attr, None)):
                bad.append(f"{name}.{attr}")
    assert not bad, f"looks callable but is actually a slot descriptor: {bad}"
    print("test_callable_looking_attributes_are_actually_callable OK")


def test_contract_is_not_accidentally_shrinking():
    """The contract is what others import against, so removing a name must be a deliberate act.

    This lower bound tracks the whitepaper; if the contract genuinely needs to shrink, update
    this alongside it, so that a removal has to be a conscious decision.
    """
    # 2026-07-23 baseline lowered: the product_hooks delegation mechanism + AgentLoop + old
    # terminology (alignment audit A1/A2/B3) were deliberately retired, a net reduction in surface.
    assert len(fa.__all__) >= 55, f"export count dropped to {len(fa.__all__)}, was this intentional?"
    print("test_contract_is_not_accidentally_shrinking OK")


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in _TESTS:
        t()
    print(f"\nALL {len(_TESTS)} KERNEL-CONTRACT TESTS PASSED")
