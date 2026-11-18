"""Projection generations: no provider calls, only immutable synthetic history."""
import asyncio
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


def test_summary_transcript_is_not_locally_capped_or_truncated():
    messages, state = history(5), fresh()
    messages[0]["content"] = json.dumps({"中文": "约束" * 45_000, "english": "keep-all-" * 12_000}, ensure_ascii=False)
    planned = {"prev_end": 0, "prev_text": "Old constraint " * 500, "new_until": 9}
    transcript = stable.summary_transcript(
        ProjectionConfig(), messages, state, planned, format_metadata=fmt, max_chars=1000
    )
    assert len(transcript) > 80_000
    assert "keep-all-" * 100 in transcript
    assert "约束" * 100 in transcript
    assert "truncated" not in transcript
    assert state["generation"] == 0


class ExplicitOverflow(RuntimeError):
    pass


def _is_overflow(exc: BaseException) -> bool:
    return isinstance(exc, ExplicitOverflow)


def test_summary_fold_first_tries_full_history_then_keeps_tool_pair_together():
    messages = [
        {"role": "user", "content": "old" * 200},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "paired", "type": "function", "function": {"name": "inspect", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "paired", "content": "result" * 200},
        {"role": "assistant", "content": "after" * 200},
        {"role": "user", "content": "new" * 200},
    ]
    calls: list[tuple[str, list[dict]]] = []

    def summarize(previous: str, segment: list[dict]) -> str:
        calls.append((previous, deepcopy(segment)))
        if len(calls) == 1:
            raise ExplicitOverflow("structured-provider-overflow")
        return f"summary-call-{len(calls)} " * 80

    telemetry = stable.SummaryFoldTelemetry()
    result = stable.fold_summary_on_context_overflow(
        "previous-generation", messages, summarize, _is_overflow, telemetry=telemetry
    )
    assert calls[0] == ("previous-generation", messages)
    assert len(calls) == 3
    assert calls[2][0].startswith("summary-call-2")
    assert result.startswith("summary-call-3")
    for _previous, segment in calls[1:]:
        assert segment and segment[0].get("role") != "tool"
        call_side = next((i for i, msg in enumerate(segment) if msg.get("tool_calls")), None)
        result_side = next((i for i, msg in enumerate(segment) if msg.get("tool_call_id") == "paired"), None)
        assert (call_side is None) == (result_side is None)
    assert telemetry.initial_overflow is True
    assert telemetry.model_calls == 3
    assert telemetry.segmented_calls == 2
    assert telemetry.split_events == 1


def test_summary_fold_recurses_with_bounded_chronological_halves():
    messages = [{"role": "user", "content": str(i) * 100} for i in range(8)]
    calls: list[tuple[str, list[dict]]] = []

    def summarize(previous: str, segment: list[dict]) -> str:
        calls.append((previous, deepcopy(segment)))
        if len(segment) > 2:
            raise ExplicitOverflow("context-length")
        return "chronological summary " * 60

    telemetry = stable.SummaryFoldTelemetry()
    result = stable.fold_summary_on_context_overflow(
        "old summary", messages, summarize, _is_overflow, telemetry=telemetry
    )
    assert result.startswith("chronological summary")
    assert calls[0][1] == messages
    assert telemetry.model_calls == 7
    assert telemetry.segmented_calls == 6
    assert telemetry.split_events == 3
    assert telemetry.max_depth == 2


def test_summary_fold_never_splits_non_overflow_or_indivisible_message():
    calls = 0

    def network_failure(_previous: str, _segment: list[dict]) -> str:
        nonlocal calls
        calls += 1
        raise RuntimeError("429 rate limit")

    with pytest.raises(RuntimeError, match="429"):
        stable.fold_summary_on_context_overflow(
            "", [{"role": "user", "content": "x"}], network_failure, _is_overflow
        )
    assert calls == 1

    child_calls = 0

    def child_network_failure(_previous: str, _segment: list[dict]) -> str:
        nonlocal child_calls
        child_calls += 1
        if child_calls == 1:
            raise ExplicitOverflow("context-length")
        if child_calls == 3:
            raise RuntimeError("429 child rate limit")
        return "intermediate summary " * 60

    child_telemetry = stable.SummaryFoldTelemetry()
    with pytest.raises(RuntimeError, match="429 child"):
        stable.fold_summary_on_context_overflow(
            "",
            [{"role": "user", "content": "first"}, {"role": "user", "content": "second"}],
            child_network_failure,
            _is_overflow,
            telemetry=child_telemetry,
        )
    assert child_calls == 3
    assert child_telemetry.split_events == 1

    def overflow(_previous: str, _segment: list[dict]) -> str:
        raise ExplicitOverflow("context-length")

    with pytest.raises(stable.ProjectionFailure, match="summary_segment_indivisible"):
        stable.fold_summary_on_context_overflow(
            "", [{"role": "user", "content": "x" * 200_000}], overflow, _is_overflow
        )


def test_summary_fold_cancellation_propagates_without_fallback():
    telemetry = stable.SummaryFoldTelemetry()

    def cancelled(_previous: str, _segment: list[dict]) -> str:
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        stable.fold_summary_on_context_overflow(
            "", [{"role": "user", "content": "x"}], cancelled, _is_overflow, telemetry=telemetry
        )
    assert telemetry.model_calls == 1
    assert telemetry.split_events == 0


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


def test_manual_force_compacts_below_threshold_preserving_tail_pairing_and_reason():
    config = ProjectionConfig()
    policy = CompactionPolicy(
        enabled=True,
        verbatim_max_tokens_abs=500_000,
        verbatim_hard_max_tokens_abs=600_000,
        verbatim_min_tail_tokens_abs=4_000,
        reserved_tail_messages=8,
    )
    messages = history(20, size=3_000)
    original = deepcopy(messages)
    state = stable.initial_state(config, policy, messages, None, format_metadata=fmt)

    # Automatic planning stays idle well below trigger.
    assert stable.plan(config, policy, messages, None, state, format_metadata=fmt,
                       window_usable=500_000, fixed_overhead_tokens=100) is None

    # Manual force skips only the trigger check, keeping tail floor + tool pairing.
    planned = stable.plan(config, policy, messages, None, state, format_metadata=fmt,
                          window_usable=500_000, fixed_overhead_tokens=100, force=True)
    assert planned is not None
    assert 0 < planned["new_until"] <= len(messages) - policy.reserved_tail_messages
    assert messages[planned["new_until"]]["role"] != "tool"

    record = CompactionRecord("cs-manual-1", planned["new_until"], "A faithful manual checkpoint summary. " * 25)
    committed = stable.next_generation(
        config, policy, messages, state, record,
        format_metadata=fmt,
        target_tokens=planned["target_tokens"],
        fixed_overhead_tokens=100,
        minimum_tail_tokens=planned["minimum_tail_tokens"],
        reason="manual_compaction",
    )
    assert committed["generation"] == 1
    assert committed["reason"] == "manual_compaction"
    assert stable.trace(committed)["reason"] == "manual_compaction"
    assert messages == original

    # Re-running manual force immediately when no further messages outside tail exist fails safely.
    with pytest.raises(stable.ProjectionFailure, match="irreducible_tail_floor"):
        stable.plan(config, policy, messages, record, committed, format_metadata=fmt,
                    window_usable=500_000, fixed_overhead_tokens=100, force=True)
