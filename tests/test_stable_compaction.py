"""Projection generations: no provider calls, only immutable synthetic history."""
from copy import deepcopy
import json

import pytest

from flops_agent.engine.compaction import CompactionPolicy, ProjectionConfig
from flops_agent.engine import stable_compaction as stable
from flops_agent.seams.compaction_store import CompactionRecord


def fmt(_meta):
    return ""


def history(count, size=20_000, name="search"):
    result = [{"role": "user", "content": "Preserve the original requirement."}]
    for i in range(count):
        result.extend([
            {"role": "assistant", "content": "", "tool_calls": [{"id": f"call-{i}", "type": "function",
             "function": {"name": name, "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": f"call-{i}", "content": f"result {i}: " + "x" * size},
        ])
    return result


def fresh(config=None, policy=None):
    config = config or ProjectionConfig()
    policy = policy or CompactionPolicy(enabled=True)
    return stable.initial_state(config, policy, history(0), None, format_metadata=fmt)


def render(messages, state, active=None, config=None):
    return stable.project(config or ProjectionConfig(), messages, active, state, format_metadata=fmt)


def test_first_tool_budget_survives_steps_retries_and_state_reload():
    state = fresh()
    first = render(history(1), state).messages[-1]
    assert 15_900 < len(first["content"]) <= 16_000
    messages = history(35)
    original = deepcopy(messages)
    reloaded = json.loads(json.dumps(state))
    a = render(messages, reloaded)
    b = render(messages, reloaded)
    assert a.messages[2] == first == b.messages[2]
    assert a.total_tokens == b.total_tokens
    assert messages == original
    assert reloaded == state


def test_exempt_tool_keeps_full_result_without_mutating_history():
    config = ProjectionConfig(tool_soft_trim_exempt_names=frozenset({"doc_read"}))
    state = fresh(config)
    messages = history(30, name="doc_read")
    assert render(messages, state, config=config).messages[2]["content"] == messages[2]["content"]


def test_legacy_adoption_is_explicit_and_then_stops_aging():
    messages = history(30, size=8000)
    config, policy = ProjectionConfig(), CompactionPolicy()
    state = stable.initial_state(config, policy, messages, None, format_metadata=fmt)
    assert state["reason"] == "legacy_adoption"
    old = render(messages, state).messages
    new = render(history(40, size=8000), json.loads(json.dumps(state))).messages
    assert new[:len(old)] == old


def test_compaction_ages_only_retained_tools_and_summary_reads_previous_view():
    state = fresh()
    messages = history(40, size=8000)
    before = render(messages, state)
    record = CompactionRecord("summary-1", 11, "A faithful summary")
    next_state = stable.next_generation(ProjectionConfig(), CompactionPolicy(), messages, state, record,
        format_metadata=fmt, target_tokens=1_000_000, fixed_overhead_tokens=50)
    after = render(messages, next_state, record)
    assert next_state["generation"] == 1
    assert len(before.messages[12]["content"]) > 8000
    first_retained_tool = next(m for m in after.messages if m.get("tool_call_id") == "call-5")
    assert len(first_retained_tool["content"]) < 200
    assert all(key not in next_state["tool_tiers"] for key in [stable.tool_identity(2, messages[2])])
    transcript = stable.summary_transcript(ProjectionConfig(), messages, state,
        {"prev_end": 0, "prev_text": "Earlier constraint must survive.", "new_until": 11},
        format_metadata=fmt, max_chars=100_000)
    assert "Earlier constraint must survive." in transcript
    assert messages[2]["content"] in transcript  # pre-downgrade text, not a stub
    later = render(history(50, size=8000), next_state, record)
    old_tools = [m for m in after.messages if m["role"] == "tool"]
    assert [m for m in later.messages if m["role"] == "tool"][:len(old_tools)] == old_tools


def test_summary_input_limit_fails_without_silently_discarding_previous_constraints():
    messages, state = history(5), fresh()
    planned = {"prev_end": 0, "prev_text": "Old constraint " * 500, "new_until": 9}
    with pytest.raises(stable.ProjectionFailure, match="summary_input_budget_exceeded"):
        stable.summary_transcript(ProjectionConfig(), messages, state, planned, format_metadata=fmt, max_chars=1000)
    assert state["generation"] == 0


def test_joint_target_rejects_infeasible_generation_and_keeps_instructions():
    messages, state = history(25), fresh()
    original = deepcopy(state)
    with pytest.raises(stable.ProjectionFailure, match="post_compaction_target_unattainable"):
        stable.next_generation(ProjectionConfig(), CompactionPolicy(), messages, state,
            CompactionRecord("new", 3, "summary" * 1000), format_metadata=fmt,
            target_tokens=200, fixed_overhead_tokens=200)
    assert state == original


def test_no_duplicate_restore_and_optional_restore_yields_to_total_budget():
    messages, state = history(15, size=2000), fresh()
    config, policy = ProjectionConfig(), CompactionPolicy()
    record = CompactionRecord("new", 11, "summary" * 100)
    wide = stable.next_generation(config, policy, messages, state, record, format_metadata=fmt,
                                  target_tokens=1_000_000, fixed_overhead_tokens=0)
    without = {**wide, "reinject_indices": []}
    minimum = render(messages, without, record).total_tokens
    tight = stable.next_generation(config, policy, messages, state, record, format_metadata=fmt,
                                   target_tokens=minimum, fixed_overhead_tokens=0)
    assert not tight["reinject_indices"]
    assert render(messages, tight, record).total_tokens == minimum
    assert all(i < wide["tail_start"] for i in wide["reinject_indices"])
    assert len(wide["reinject_indices"]) == len(set(wide["reinject_indices"]))


def test_pair_guard_history_rewrite_and_torn_summary_are_rejected():
    messages = history(5)
    state = stable.initial_state(ProjectionConfig(), CompactionPolicy(), messages, None, format_metadata=fmt)
    rewritten = deepcopy(messages)
    rewritten[2]["content"] = "changed"
    with pytest.raises(stable.ProjectionFailure, match="history_changed"):
        render(rewritten, state)
    with pytest.raises(stable.ProjectionFailure, match="torn_context_generation"):
        render(messages, state, CompactionRecord("wrong", 3, "summary"))
    with pytest.raises(stable.ProjectionFailure, match="splits_tool_pair"):
        stable.next_generation(ProjectionConfig(), CompactionPolicy(), messages, state,
            CompactionRecord("split", 2, "summary"), format_metadata=fmt,
            target_tokens=100_000, fixed_overhead_tokens=0)


def test_new_images_do_not_age_previous_tool_or_user_images():
    messages = history(0)
    state = fresh()
    for i in range(5):
        messages.append({"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"synthetic://{i}"}}]})
        current = render(messages, state).messages
        if i:
            assert current[:-1] == previous
        previous = current


def test_parameters_are_frozen_until_next_generation():
    state = fresh()
    changed = ProjectionConfig(proj_recent_tool_full_max_chars=500)
    assert render(history(1), state, config=changed).messages == render(history(1), state).messages
    assert stable.trace(state)["parameters_sha256"] == stable.digest(state["parameters"])
