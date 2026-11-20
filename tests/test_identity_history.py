"""v3 identity-chain generations: the persistence layer's ids authenticate history.

Contract under test: the framework only hashes the identity field named in the
state. It never records content-derived digests for v3, it refuses to certify a
prefix containing uncommitted (unidentified) turns, and it re-keys a v2 state only
after the content chain has just matched.
"""
import json

import pytest

from flops_agent.engine import stable_compaction as stable
from flops_agent.engine.compaction import CompactionPolicy, ProjectionConfig
from flops_agent.seams.compaction_store import CompactionRecord

FIELD = "_id"


def fmt(_message):
    return ""


def history(rounds, *, ids=True):
    out = [{"role": "user", "content": "instruction"}]
    for i in range(rounds):
        out += [{"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}", "type": "function",
                 "function": {"name": "t", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": f"c{i}", "content": f"result {i} " + "x" * 400},
                {"role": "assistant", "content": f"done {i}"},
                {"role": "user", "content": f"next {i}"}]
    if ids:
        for index, message in enumerate(out):
            message[FIELD] = f"id-{index}"
    return out


def v3_generation(messages, *, end):
    state = stable.initial_state(ProjectionConfig(), CompactionPolicy(), messages[:1], None,
                                 format_metadata=fmt, identity_field=FIELD)
    record = CompactionRecord(f"s{end}", end, "summary")
    return stable.next_generation(ProjectionConfig(), CompactionPolicy(), messages, state, record,
                                  format_metadata=fmt, target_tokens=10**9, fixed_overhead_tokens=0), record


def test_v3_state_records_only_identity_digests():
    messages = history(6)
    state, _ = v3_generation(messages, end=9)
    assert state["history_digest_version"] == stable.IDENTITY_DIGEST_VERSION
    assert state["history_identity_field"] == FIELD
    serialized = json.dumps(state)
    for message in messages:
        assert stable.message_digest(message) not in serialized   # no content digest stored
    assert state["prefix_history_sha256"] == stable.chain_digest(messages, 0, 9, identity_field=FIELD)


def test_content_change_under_same_id_is_invisible_and_new_id_is_detected():
    """The invariant is the persistence layer's job: same id means same content."""
    messages = history(6)
    state, _ = v3_generation(messages, end=9)
    edited = [dict(m) for m in messages]
    edited[2]["content"] = "edited in place without a new id"
    assert stable.check_history(state, edited).status == "match"
    edited[2][FIELD] = "id-2-reissued"
    assert stable.check_history(state, edited).status == "prefix_changed"


@pytest.mark.parametrize("mutation", ["delete", "insert", "swap"])
def test_prefix_deletion_insertion_and_reordering_are_detected(mutation):
    messages = history(6)
    state, _ = v3_generation(messages, end=9)
    changed = [dict(m) for m in messages]
    if mutation == "delete":
        del changed[3]
    elif mutation == "insert":
        changed.insert(4, {"role": "user", "content": "task event", "isMeta": True, FIELD: "inserted"})
    else:
        changed[3], changed[4] = changed[4], changed[3]
    assert stable.check_history(state, changed).status == "prefix_changed"


def test_tail_rewrite_keeps_summary_and_reanchors():
    messages = history(6)
    state, record = v3_generation(messages, end=9)
    changed = [dict(m) for m in messages]
    changed[-2] = {**changed[-2], FIELD: "tail-reissued"}
    verdict = stable.check_history(state, changed)
    assert verdict.status == "tail_rewritten"
    reanchored = stable.reanchor_state(state, changed)
    assert reanchored["active_summary_id"] == record.id and reanchored["tail_start"] == 9
    assert reanchored["history_digest_version"] == stable.IDENTITY_DIGEST_VERSION
    assert stable.check_history(reanchored, changed).status == "match"


def test_uncommitted_tail_turn_is_allowed_but_uncommitted_prefix_is_refused():
    messages = history(6)
    state, _ = v3_generation(messages, end=9)
    pending_tail = messages + [{"role": "user", "content": "not stored yet"}]
    assert stable.check_history(state, pending_tail).status == "match"
    pending_prefix = [dict(m) for m in messages]
    del pending_prefix[1][FIELD]
    with pytest.raises(stable.ProjectionFailure, match="history_identity_incomplete"):
        stable.check_history(state, pending_prefix)
    with pytest.raises(stable.ProjectionFailure, match="history_identity_incomplete"):
        stable.next_generation(ProjectionConfig(), CompactionPolicy(), pending_prefix, state,
                               CompactionRecord("s13", 13, "summary"), format_metadata=fmt,
                               target_tokens=10**9, fixed_overhead_tokens=0)


def test_fallback_checkpoint_in_identity_mode():
    messages = history(6)
    first, first_record = v3_generation(messages, end=5)
    second = stable.next_generation(ProjectionConfig(), CompactionPolicy(), messages, first,
                                    CompactionRecord("s13", 13, "summary"), format_metadata=fmt,
                                    target_tokens=10**9, fixed_overhead_tokens=0)
    changed = [dict(m) for m in messages]
    changed[8] = {**changed[8], FIELD: "reissued-8"}       # inside the newest summary only
    verdict = stable.check_history(second, changed)
    assert verdict.status == "prefix_changed" and verdict.fallback["summary_id"] == first_record.id
    rebased = stable.rebase_state(ProjectionConfig(), second, changed, first_record)
    assert rebased["tail_start"] == 5 and rebased["history_digest_version"] == stable.IDENTITY_DIGEST_VERSION
    assert stable.check_history(rebased, changed).status == "match"


def test_adopt_identity_requires_a_fresh_content_match_and_keeps_decisions():
    messages = history(6)
    v2 = stable.initial_state(ProjectionConfig(), CompactionPolicy(), messages[:1], None, format_metadata=fmt)
    v2 = stable.next_generation(ProjectionConfig(), CompactionPolicy(), messages, v2,
                                CompactionRecord("s9", 9, "summary"), format_metadata=fmt,
                                target_tokens=10**9, fixed_overhead_tokens=0)
    assert v2["history_digest_version"] == stable.HISTORY_DIGEST_VERSION
    edited = [dict(m) for m in messages]
    edited[2]["content"] = "edited"
    with pytest.raises(stable.ProjectionFailure, match="history_changed"):
        stable.adopt_identity(v2, edited, FIELD)
    v3 = stable.adopt_identity(v2, messages, FIELD)
    for key in ("projection_id", "generation", "reason", "active_summary_id", "tail_start", "tool_tiers"):
        assert v3[key] == v2[key]
    assert v3["identity_adopted_from"] == stable.HISTORY_DIGEST_VERSION
    assert [cp["summary_id"] for cp in v3["summary_checkpoints"]] == ["s9"]
    assert stable.check_history(v3, messages).status == "match"
    assert stable.adopt_identity(v3, messages, FIELD) is v3
    unidentified = [{k: v for k, v in m.items() if k != FIELD} for m in messages]
    with pytest.raises(stable.ProjectionFailure, match="history_identity_incomplete"):
        stable.adopt_identity(v2, unidentified, FIELD)


def test_adopt_identity_drops_checkpoints_it_cannot_prove():
    messages = history(6)
    v2 = stable.initial_state(ProjectionConfig(), CompactionPolicy(), messages[:1], None, format_metadata=fmt)
    v2 = stable.next_generation(ProjectionConfig(), CompactionPolicy(), messages, v2,
                                CompactionRecord("s5", 5, "summary"), format_metadata=fmt,
                                target_tokens=10**9, fixed_overhead_tokens=0)
    v2 = stable.next_generation(ProjectionConfig(), CompactionPolicy(), messages, v2,
                                CompactionRecord("s13", 13, "summary"), format_metadata=fmt,
                                target_tokens=10**9, fixed_overhead_tokens=0)
    forged = {**v2, "summary_checkpoints": [{**v2["summary_checkpoints"][0], "prefix_sha256": "0" * 64},
                                            v2["summary_checkpoints"][1]]}
    v3 = stable.adopt_identity(forged, messages, FIELD)
    assert [cp["summary_id"] for cp in v3["summary_checkpoints"]] == ["s13"]


def test_v2_mode_is_unchanged_without_identity_field():
    messages = history(6, ids=False)
    v2 = stable.initial_state(ProjectionConfig(), CompactionPolicy(), messages[:1], None, format_metadata=fmt)
    assert v2["history_digest_version"] == stable.HISTORY_DIGEST_VERSION
    assert "history_identity_field" not in v2
