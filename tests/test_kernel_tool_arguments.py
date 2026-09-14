"""Tool argument parsing belongs to the framework: valid / repairable / truncated / invalid / not-an-object — all five outcomes must be clearly distinguished."""
import json
import os
import sys
from types import SimpleNamespace as SN

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from flops_agent.tools.schema import (  # noqa: E402
    FAIL_INVALID_JSON, FAIL_NOT_OBJECT, FAIL_TRUNCATED,
    json_brackets_balanced, parse_tool_arguments, repair_unquoted_json_values,
)


def test_good_arguments_pass_through():
    for raw, want in (
        ('{"a": 1, "b": "x"}', {"a": 1, "b": "x"}),
        ("{}", {}),
        ("", {}),            # a no-argument tool sending an empty string is success, not failure
        ("   ", {}),
        (None, {}),
        ({"already": "dict"}, {"already": "dict"}),   # some providers give an object directly
    ):
        p = parse_tool_arguments(raw)
        assert p.ok and p.arguments == want and p.fail_kind == "" and not p.repaired, raw
    # The accepted dict is a copy; mutating it should not write back to the caller
    src = {"a": 1}
    parse_tool_arguments(src).arguments["a"] = 2
    assert src == {"a": 1}
    print("test_good_arguments_pass_through OK")


def test_unquoted_bare_value_is_repaired():
    """Malformed output the model occasionally produces with CJK and full-width punctuation: the value after a colon runs bare with no quotes. A successful repair still keeps the original error."""
    raw = '{"q": ["x"], "search_goal": research…information, "mode": "y"}'
    p = parse_tool_arguments(raw)
    assert p.ok and p.repaired
    assert p.arguments == {"q": ["x"], "search_goal": "research…information", "mode": "y"}
    assert p.error and p.fail_kind == ""
    # Newlines and quotes inside the bare value must be escaped; the repair should still yield valid JSON
    fixed = repair_unquoted_json_values('{"a": he said "ok"\ncontinued, "b": 1}')
    assert fixed is not None and json.loads(fixed)["a"] == 'he said "ok"\ncontinued'
    # No bare value to repair -> None, don't pretend a repair happened
    assert repair_unquoted_json_values('{"a": "b"}') is None
    assert repair_unquoted_json_values("") is None
    print("test_unquoted_bare_value_is_repaired OK")


def test_failure_kinds_are_distinguished():
    """Truncation and syntactically-invalid must be distinguished: the former is worth retrying, the latter needs the model to rewrite its call."""
    truncated = parse_tool_arguments('{"command": "ls -la')
    assert not truncated.ok and truncated.fail_kind == FAIL_TRUNCATED and truncated.arguments == {}
    invalid = parse_tool_arguments('{"a" 1}')
    assert not invalid.ok and invalid.fail_kind == FAIL_INVALID_JSON and invalid.error
    not_object = parse_tool_arguments("[1, 2]")
    assert not not_object.ok and not_object.fail_kind == FAIL_NOT_OBJECT
    assert parse_tool_arguments("42").fail_kind == FAIL_NOT_OBJECT
    # Bracket-balance detection ignores brackets inside string literals
    assert json_brackets_balanced('{"a": "}{"}') and not json_brackets_balanced('{"a": [1}')
    assert not json_brackets_balanced('{"a": "unclosed')
    print("test_failure_kinds_are_distinguished OK")


def test_default_executor_reports_bad_arguments_instead_of_running_empty():
    """Bad arguments must not silently become an empty dict — an empty dict makes the tool run as if "nothing was given", which is more dangerous than an outright error."""
    import asyncio

    from flops_agent import ToolContext, ToolRegistry
    from flops_agent.seams.executor import DefaultToolExecutor

    seen = []

    async def echo(arguments, ctx):
        seen.append(arguments)
        return {"ok": True, "got": arguments}

    reg = ToolRegistry()
    reg.register_tool("/tools", "echo", {"type": "function", "function": {"name": "echo", "description": "e", "parameters": {"type": "object", "properties": {}}}})
    reg.register_unified_handler("/tools", "echo", echo)

    def ctx():
        return ToolContext(user_id="u", session_id="s", function_name="echo", tool_domains=["/tools"], registry=reg)

    ex = DefaultToolExecutor()
    good = asyncio.run(ex.execute(SN(function=SN(name="echo", arguments='{"a": 1}')), ctx()))
    assert good.value == {"ok": True, "got": {"a": 1}} and good.ok and seen == [{"a": 1}]

    bad = asyncio.run(ex.execute(SN(function=SN(name="echo", arguments='{"a": ')), ctx()))
    assert bad.value["success"] is False and bad.value["arguments_fail_kind"] == FAIL_TRUNCATED and not bad.ok
    assert len(seen) == 1, "bad arguments must not reach the handler"
    print("test_default_executor_reports_bad_arguments_instead_of_running_empty OK")


if __name__ == "__main__":
    test_good_arguments_pass_through()
    test_unquoted_bare_value_is_repaired()
    test_failure_kinds_are_distinguished()
    test_default_executor_reports_bad_arguments_instead_of_running_empty()
