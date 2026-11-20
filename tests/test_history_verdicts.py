"""History digests, verdicts and recovery states for stable compaction generations."""
import pytest

from flops_agent.engine import stable_compaction as stable
from flops_agent.engine.compaction import CompactionPolicy, ProjectionConfig
from flops_agent.seams.compaction_store import CompactionRecord


def fmt(_message):
    return ""


def history(rounds):
    out = [{"role": "user", "content": "instruction"}]
    for i in range(rounds):
        out += [{"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}", "type": "function",
                 "function": {"name": "t", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": f"c{i}", "content": f"result {i} " + "x" * 400},
                {"role": "assistant", "content": f"done {i}"},
                {"role": "user", "content": f"next {i}"}]
    return out


def stored(messages):
    """What a column store returns: nullable fields materialized as None, ids added."""
    return [{**{k: None for k in ("content", "tool_calls", "tool_call_id", "reasoning_content")}, **m,
             "_msg_id": f"m{i}"} for i, m in enumerate(messages)]


def generation(messages, *, end):
    state = stable.initial_state(ProjectionConfig(), CompactionPolicy(), messages[:1], None, format_metadata=fmt)
    record = CompactionRecord(f"s{end}", end, "summary")
    return stable.next_generation(ProjectionConfig(), CompactionPolicy(), messages, state, record,
                                  format_metadata=fmt, target_tokens=10**9, fixed_overhead_tokens=0), record


def test_v2_matches_across_storage_shape_and_append():
    messages = history(6)
    state, _ = generation(messages, end=9)
    assert state["history_digest_version"] == 2
    assert stable.history_matches(state, stored(messages))
    assert stable.history_matches(state, stored(messages) + [{"role": "user", "content": "new"}])
    assert stable.check_history(state, messages).status == "match"


def test_legacy_v1_accepts_either_uniform_shape_only_for_equal_content():
    messages = history(4)
    state, _ = generation(messages, end=9)
    start, count = state["tail_start"], state["boundary_count"]
    for hashed in (messages, stored(messages)):
        legacy = {k: v for k, v in state.items() if k not in (
            "history_digest_version", "prefix_history_sha256", "prefix_last_message_sha256", "summary_checkpoints")}
        legacy["boundary_history_sha256"] = stable.history_digest(hashed[start:count])
        assert stable.history_matches(legacy, messages)
        assert stable.history_matches(legacy, stored(messages))
        edited = [dict(m) for m in messages]
        edited[-1]["content"] = "edited"
        assert not stable.history_matches(legacy, edited)
        assert stable.check_history(legacy, edited).status == "unverifiable"
        assert stable.check_history(legacy, messages[:start - 1]).status == "prefix_changed"
        upgraded = stable.upgrade_legacy_state(legacy, stored(messages), None)
        assert upgraded["projection_id"] == legacy["projection_id"]
        assert stable.history_matches(upgraded, messages)


def test_tail_rewrite_reanchors_and_keeps_unchanged_tool_tiers():
    messages = history(6)
    state, _ = generation(messages, end=9)
    state["tool_tiers"] = {stable.tool_identity(i, messages[i]): "stub"
                           for i in range(state["tail_start"], len(messages)) if messages[i]["role"] == "tool"}
    rewritten = messages[:-2] + [{"role": "assistant", "content": "regenerated"}]
    verdict = stable.check_history(state, rewritten)
    assert verdict.status == "tail_rewritten"
    new = stable.reanchor_state(state, rewritten)
    assert new["tail_start"] == state["tail_start"] and new["active_summary_id"] == state["active_summary_id"]
    assert new["generation"] == state["generation"] + 1 and new["boundary_count"] == len(rewritten)
    assert set(new["tool_tiers"]) <= set(state["tool_tiers"]) and set(new["tool_tiers"].values()) == {"stub"}
    assert stable.check_history(new, rewritten).status == "match"


def test_prefix_rewrite_falls_back_to_exact_checkpoint_or_none():
    messages = history(8)
    first, first_record = generation(messages, end=9)
    second = stable.next_generation(ProjectionConfig(), CompactionPolicy(), messages, first,
                                    CompactionRecord("s21", 21, "newer"), format_metadata=fmt,
                                    target_tokens=10**9, fixed_overhead_tokens=0)
    assert [cp["summary_id"] for cp in second["summary_checkpoints"]] == ["s9", "s21"]
    # resumed digest equals a from-scratch digest of the same prefix
    assert second["prefix_history_sha256"] == stable.chain_digest(messages, 0, 21)

    rewound = messages[:13] + [{"role": "assistant", "content": "other answer"}]
    verdict = stable.check_history(second, rewound)
    assert verdict.status == "prefix_changed" and verdict.fallback["summary_id"] == "s9"
    rebased = stable.rebase_state(ProjectionConfig(), second, rewound, first_record)
    assert rebased["tail_start"] == 9 and rebased["active_summary_id"] == "s9"
    assert stable.check_history(rebased, rewound).status == "match"
    with pytest.raises(stable.ProjectionFailure, match="summary_checkpoint_mismatch"):
        stable.rebase_state(ProjectionConfig(), second, rewound, CompactionRecord("s21", 21, "newer"))

    early = messages[:5] + [{"role": "assistant", "content": "other"}]
    verdict = stable.check_history(second, early)
    assert verdict.status == "prefix_changed" and verdict.fallback is None
    none = stable.rebase_state(ProjectionConfig(), second, early, None)
    assert none["tail_start"] == 0 and none["active_summary_id"] is None and none["summary_checkpoints"] == []
    projection = stable.project(ProjectionConfig(), early, None, none, format_metadata=fmt)
    assert len(projection.messages) == len(early)  # all raw history, no summary


def test_last_summarized_message_is_pinned():
    messages = history(6)
    state, _ = generation(messages, end=9)
    edited = [dict(m) for m in messages]
    edited[8] = {**edited[8], "content": "changed just before the boundary"}
    assert not stable.history_matches(state, edited)
    assert stable.check_history(state, edited).status == "prefix_changed"


def test_chain_digest_is_resumable_and_positional():
    messages = history(3)
    mid = stable.chain_digest(messages, 0, 4)
    assert stable.chain_digest(messages, 4, seed=mid) == stable.chain_digest(messages)
    swapped = [messages[1], messages[0]] + messages[2:]
    assert stable.history_digest_v2(swapped) != stable.history_digest_v2(messages)


def test_early_prefix_edit_is_detected_even_when_tail_and_predecessor_match():
    messages = history(6)
    state, active = generation(messages, end=9)
    edited = [dict(m) for m in messages]
    edited[0]["content"] = "changed source"
    assert stable.history_matches(state, edited)
    assert stable.check_history(state, edited).status == "prefix_changed"
    with pytest.raises(stable.ProjectionFailure, match="history_changed_since_context_boundary"):
        stable.project(ProjectionConfig(), edited, active, state, format_metadata=fmt)
